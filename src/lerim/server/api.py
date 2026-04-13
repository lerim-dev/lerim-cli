"""Shared API logic for CLI and HTTP endpoints.

Extracts the core business logic for ask, sync, maintain, and project
management so both the argparse CLI and the HTTP API call the same code.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
	from collections.abc import Generator

from lerim import __version__
from lerim.adapters.registry import (
    connect_platform,
    list_platforms,
)
from lerim.memory.repo import build_memory_paths, ensure_project_memory
from lerim.server.daemon import (
    resolve_window_bounds,
    run_maintain_once,
    run_sync_once,
)
from lerim.config.settings import (
    Config,
    get_config,
    load_toml_file,
    reload_config,
    save_config_patch,
    _write_config_full,
    USER_CONFIG_PATH,
)
from lerim.server.runtime import LerimRuntime
from lerim.sessions.catalog import (
    count_fts_indexed,
    count_session_jobs_by_status,
    count_unscoped_sessions_by_agent,
    latest_service_run,
    list_queue_jobs,
    list_unscoped_sessions,
    queue_health_snapshot,
    retry_session_job,
    skip_session_job,
)


# ── Argument parsing helpers (inlined from arg_utils.py) ────────────


def parse_duration_to_seconds(raw: str) -> int:
	"""Parse ``<number><unit>`` durations like ``30s`` or ``7d`` to seconds."""
	value = (raw or "").strip().lower()
	if len(value) < 2:
		raise ValueError("duration must be <number><unit>, for example: 30s, 2m, 1h, 7d")
	unit = value[-1]
	amount_text = value[:-1]
	if not amount_text.isdigit():
		raise ValueError("duration must be <number><unit>, for example: 30s, 2m, 1h, 7d")
	amount = int(amount_text)
	if amount <= 0:
		raise ValueError("duration must be greater than 0")
	multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
	if unit not in multipliers:
		raise ValueError("duration unit must be one of: s, m, h, d")
	return amount * multipliers[unit]


def parse_csv(raw: str | None) -> list[str]:
	"""Split a comma-delimited string into trimmed non-empty values."""
	if not raw:
		return []
	return [part.strip() for part in raw.split(",") if part.strip()]


def parse_agent_filter(raw: str | None) -> list[str] | None:
	"""Normalize agent filter input and drop the ``all`` sentinel."""
	values = parse_csv(raw)
	cleaned = [value for value in values if value and value != "all"]
	if not cleaned:
		return None
	return sorted(set(cleaned))


def looks_like_auth_error(response: str) -> bool:
    """Return whether response text indicates authentication failure."""
    text = str(response or "").lower()
    return (
        "failed to authenticate" in text
        or "authentication_error" in text
        or "oauth token has expired" in text
        or "invalid api key" in text
        or "unauthorized" in text
    )


# ── Ollama model lifecycle (inlined from ollama_lifecycle.py) ───────


def _ollama_models(config: Config) -> list[tuple[str, str]]:
	"""Return deduplicated (base_url, model) pairs for all ollama roles."""
	seen: set[tuple[str, str]] = set()
	pairs: list[tuple[str, str]] = []

	default_base = config.provider_api_bases.get("ollama", "http://127.0.0.1:11434")

	for role in (config.agent_role,):
		if role.provider == "ollama":
			base = role.api_base or default_base
			key = (base, role.model)
			if key not in seen:
				seen.add(key)
				pairs.append(key)

	return pairs


def _is_ollama_reachable(base_url: str, timeout: float = 5.0) -> bool:
	"""Check if Ollama is reachable at the given base URL."""
	try:
		resp = httpx.get(f"{base_url}/api/tags", timeout=timeout)
		return resp.status_code == 200
	except (httpx.ConnectError, httpx.TimeoutException, OSError):
		return False


def _load_model(base_url: str, model: str, timeout: float = 120.0) -> None:
	"""Warm-load an Ollama model by sending a minimal generation request."""
	httpx.post(
		f"{base_url}/api/generate",
		json={"model": model, "prompt": "hi", "options": {"num_predict": 1}},
		timeout=timeout,
	)


def _unload_model(base_url: str, model: str, timeout: float = 30.0) -> None:
	"""Unload an Ollama model by setting keep_alive to 0."""
	httpx.post(
		f"{base_url}/api/generate",
		json={"model": model, "keep_alive": 0},
		timeout=timeout,
	)


@contextmanager
def ollama_lifecycle(config: Config) -> Generator[None, None, None]:
	"""Context manager that loads Ollama models on enter and unloads on exit.

	No-op when no roles use provider="ollama" or when auto_unload is False.
	Logs warnings on failure but never raises — the daemon must not crash
	because of lifecycle issues.
	"""
	if config.agent_role.provider != "ollama":
		yield
		return

	from lerim.config.logging import logger

	models = _ollama_models(config)

	if not models:
		yield
		return

	# Group models by base_url for a single reachability check per server.
	bases = {base for base, _ in models}

	reachable_bases: set[str] = set()
	for base in bases:
		if _is_ollama_reachable(base):
			reachable_bases.add(base)
		else:
			logger.warning("ollama not reachable at {}, skipping lifecycle", base)

	# Warm-load models on reachable servers.
	for base, model in models:
		if base not in reachable_bases:
			continue
		try:
			logger.info("loading ollama model {}/{}", base, model)
			_load_model(base, model)
		except Exception as exc:
			logger.warning("failed to warm-load {}/{}: {}", base, model, exc)

	try:
		yield
	finally:
		if not config.auto_unload:
			return

		for base, model in models:
			if base not in reachable_bases:
				continue
			try:
				logger.info("unloading ollama model {}/{}", base, model)
				_unload_model(base, model)
			except Exception as exc:
				logger.warning("failed to unload {}/{}: {}", base, model, exc)


# ── Known agent default paths ───────────────────────────────────────

AGENT_DEFAULT_PATHS: dict[str, str] = {
    "claude": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "cursor": "~/Library/Application Support/Cursor/User/globalStorage",
    "opencode": "~/.local/share/opencode",
}


def api_health() -> dict[str, Any]:
    """Return health check payload."""
    return {"status": "ok", "version": __version__}


def _registered_projects(config: Config) -> list[tuple[str, Path]]:
    """Return registered projects as resolved (name, path) pairs."""
    items: list[tuple[str, Path]] = []
    for name, path_str in config.projects.items():
        items.append((name, Path(path_str).expanduser().resolve()))
    return items


def _project_memory_root(project_path: Path) -> Path:
    """Return per-project memory root path."""
    return project_path / ".lerim" / "memory"


def _ensure_memory_root_layout(memory_root: Path) -> None:
    """Ensure memory root has canonical folders + index file."""
    ensure_project_memory(build_memory_paths(memory_root.parent))
    index_path = memory_root / "index.md"
    if not index_path.exists():
        index_path.write_text("# Memory Index\n", encoding="utf-8")


def _resolve_selected_projects(
    *,
    config: Config,
    scope: str,
    project: str | None,
) -> list[tuple[str, Path]]:
    """Resolve target projects for scoped read/query APIs."""
    all_projects = _registered_projects(config)
    if scope != "project":
        return all_projects

    if project:
        token = project.strip()
        if token in config.projects:
            return [(token, Path(config.projects[token]).expanduser().resolve())]
        try:
            project_path = Path(token).expanduser().resolve()
        except Exception:
            project_path = None
        if project_path is not None:
            for name, path in all_projects:
                if path == project_path:
                    return [(name, path)]
        raise ValueError(f"Project not found: {project}")

    if len(all_projects) == 1:
        return [all_projects[0]]
    if not all_projects:
        return []
    raise ValueError("scope=project requires a project name when multiple projects are registered.")


def _copy_memory_file(src: Path, dst_dir: Path, *, prefix: str) -> str:
    """Copy one memory markdown file into merged ask memory root."""
    safe_prefix = prefix.replace("/", "_").replace("\\", "_")
    target_name = f"{safe_prefix}__{src.name}"
    target = dst_dir / target_name
    if target.exists():
        stem = src.stem
        suffix = src.suffix
        counter = 2
        while True:
            candidate = dst_dir / f"{safe_prefix}__{stem}_{counter}{suffix}"
            if not candidate.exists():
                target = candidate
                target_name = candidate.name
                break
            counter += 1
    shutil.copy2(src, target)
    return target_name


def _build_merged_ask_memory_root(
    selected_projects: list[tuple[str, Path]],
) -> tuple[Path, str]:
    """Build temporary merged memory root for ask scope=all."""
    temp_root = Path(tempfile.mkdtemp(prefix="lerim-ask-"))
    memory_root = temp_root / "memory"
    _ensure_memory_root_layout(memory_root)
    index_lines = ["# Memory Index", ""]
    copied_any = False

    for name, project_path in selected_projects:
        src_root = _project_memory_root(project_path)
        if not src_root.exists():
            continue
        copied_names: list[str] = []
        for src in sorted(src_root.glob("*.md")):
            if src.name == "index.md":
                continue
            copied_names.append(_copy_memory_file(src, memory_root, prefix=name))
        if not copied_names:
            continue
        copied_any = True
        index_lines.append(f"## {name}")
        for copied in copied_names:
            index_lines.append(f"- [{copied}]({copied})")
        index_lines.append("")

    if not copied_any:
        index_lines.extend(["## No Memories Yet", "- No project memories found.", ""])
    (memory_root / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    return memory_root, str(temp_root)


def _count_memory_files(memory_root: Path) -> int:
    """Count markdown memory files under one memory root."""
    if not memory_root.exists():
        return 0
    return sum(1 for _ in memory_root.rglob("*.md"))


def _queue_counts_for_repo(
    *,
    sessions_db_path: Path,
    repo_path: str,
) -> tuple[dict[str, int], str | None, str | None]:
    """Return queue counts + oldest dead-letter blocker + latest error for repo."""
    counts: dict[str, int] = {}
    blocked_run_id: str | None = None
    last_error: str | None = None
    if not sessions_db_path.exists():
        return counts, blocked_run_id, last_error

    try:
        with sqlite3.connect(sessions_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT status, COUNT(1) AS total
                FROM session_jobs
                WHERE repo_path = ?
                GROUP BY status
                """,
                (repo_path,),
            ).fetchall()
            counts = {str(row["status"]): int(row["total"]) for row in rows}

            oldest = conn.execute(
                """
                SELECT run_id, status
                FROM session_jobs
                WHERE repo_path = ?
                  AND status IN ('pending', 'failed', 'dead_letter')
                ORDER BY start_time ASC, available_at ASC, id ASC
                LIMIT 1
                """,
                (repo_path,),
            ).fetchone()
            if oldest and str(oldest["status"]) == "dead_letter":
                blocked_run_id = str(oldest["run_id"])

            latest_err = conn.execute(
                """
                SELECT error
                FROM session_jobs
                WHERE repo_path = ?
                  AND status IN ('failed', 'dead_letter')
                  AND error IS NOT NULL
                  AND error != ''
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (repo_path,),
            ).fetchone()
            if latest_err:
                last_error = str(latest_err["error"])
    except sqlite3.Error:
        return {}, None, None

    for status in ("pending", "running", "done", "failed", "dead_letter"):
        counts.setdefault(status, 0)
    return counts, blocked_run_id, last_error


def api_ask(
    question: str,
    *,
    scope: str = "all",
    project: str | None = None,
) -> dict[str, Any]:
    """Run one ask query against the runtime agent and return result dict."""
    config = get_config()
    selected_projects: list[tuple[str, Path]] = []
    normalized_scope = "project" if str(scope).strip().lower() == "project" else "all"
    try:
        selected_projects = _resolve_selected_projects(
            config=config,
            scope=normalized_scope,
            project=project,
        )
    except ValueError as exc:
        return {
            "answer": str(exc),
            "agent_session_id": "",
            "memories_used": [],
            "error": True,
            "cost_usd": 0.0,
        }

    cleanup_root: str | None = None
    if normalized_scope == "all" and len(selected_projects) > 1:
        memory_root_path, cleanup_root = _build_merged_ask_memory_root(selected_projects)
    elif selected_projects:
        memory_root_path = _project_memory_root(selected_projects[0][1])
        _ensure_memory_root_layout(memory_root_path)
    else:
        memory_root_path = config.global_data_dir / "memory"
        _ensure_memory_root_layout(memory_root_path)

    agent = LerimRuntime()
    try:
        response, session_id, cost_usd = agent.ask(
            question, cwd=str(config.global_data_dir), memory_root=str(memory_root_path)
        )
    finally:
        if cleanup_root:
            shutil.rmtree(cleanup_root, ignore_errors=True)
    error = looks_like_auth_error(response)
    return {
        "answer": response,
        "agent_session_id": session_id,
        "memories_used": [name for name, _ in selected_projects],
        "error": bool(error),
        "cost_usd": cost_usd,
        "scope": normalized_scope,
    }


def api_sync(
    agent: str | None = None,
    window: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_sessions: int | None = None,
    run_id: str | None = None,
    no_extract: bool = False,
    force: bool = False,
    dry_run: bool = False,
    ignore_lock: bool = False,
) -> dict[str, Any]:
    """Run one sync cycle and return summary dict."""
    config = get_config()
    window_start, window_end = resolve_window_bounds(
        window=window or f"{config.sync_window_days}d",
        since_raw=since,
        until_raw=until,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )
    with ollama_lifecycle(config):
        code, summary = run_sync_once(
            run_id=run_id,
            agent_filter=parse_agent_filter(agent) if agent else None,
            no_extract=no_extract,
            force=force,
            max_sessions=max_sessions or config.sync_max_sessions,
            dry_run=dry_run,
            ignore_lock=ignore_lock,
            trigger="api",
            window_start=window_start,
            window_end=window_end,
        )
    queue_health = queue_health_snapshot()
    payload: dict[str, Any] = {"code": code, **asdict(summary), "queue_health": queue_health}
    if queue_health.get("degraded"):
        payload["warning"] = (
            "Queue degraded. "
            + str(queue_health.get("advice") or "Run `lerim queue --failed`.")
        )
    return payload


def api_maintain(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Run one maintain cycle and return result dict."""
    config = get_config()
    with ollama_lifecycle(config):
        code, payload = run_maintain_once(force=force, dry_run=dry_run)
    queue_health = queue_health_snapshot()
    result: dict[str, Any] = {"code": code, **payload, "queue_health": queue_health}
    if queue_health.get("degraded"):
        result["warning"] = (
            "Queue degraded. "
            + str(queue_health.get("advice") or "Run `lerim queue --failed`.")
        )
    return result


def api_status(
    *,
    scope: str = "all",
    project: str | None = None,
) -> dict[str, Any]:
    """Return runtime status summary."""
    config = get_config()
    normalized_scope = "project" if str(scope).strip().lower() == "project" else "all"
    selection_error: str | None = None
    try:
        selected_projects = _resolve_selected_projects(
            config=config,
            scope=normalized_scope,
            project=project,
        )
    except ValueError as exc:
        selection_error = str(exc)
        selected_projects = []

    projects_payload: list[dict[str, Any]] = []
    total_memory = 0
    for name, path in selected_projects:
        memory_root = _project_memory_root(path)
        memory_count = _count_memory_files(memory_root)
        total_memory += memory_count
        queue_counts, blocked_run_id, last_error = _queue_counts_for_repo(
            sessions_db_path=config.sessions_db_path,
            repo_path=str(path),
        )
        projects_payload.append(
            {
                "name": name,
                "path": str(path),
                "exists": path.exists(),
                "memory_dir": str(memory_root),
                "memory_count": memory_count,
                "queue": queue_counts,
                "oldest_blocked_run_id": blocked_run_id,
                "last_error": last_error,
            }
        )
    if not projects_payload and normalized_scope == "all":
        total_memory = _count_memory_files(config.global_data_dir / "memory")

    latest_sync = latest_service_run("sync")
    latest_maintain = latest_service_run("maintain")
    queue = count_session_jobs_by_status()
    queue_health = queue_health_snapshot()
    latest_sync_details = (latest_sync or {}).get("details") or {}
    unscoped_by_agent = count_unscoped_sessions_by_agent(projects=config.projects)

    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connected_agents": list(config.agents.keys()),
        "platforms": list_platforms(config.platforms_path),
        "memory_count": total_memory,
        "sessions_indexed_count": count_fts_indexed(),
        "queue": queue,
        "queue_health": queue_health,
        "projects": projects_payload,
        "unscoped_sessions": {
            "total": sum(unscoped_by_agent.values()),
            "by_agent": unscoped_by_agent,
        },
        "scope": {
            "strict_project_only": True,
            "mode": normalized_scope,
            "skipped_unscoped": int(latest_sync_details.get("skipped_unscoped") or 0),
        },
        "latest_sync": latest_sync,
        "latest_maintain": latest_maintain,
    }
    if selection_error:
        payload["error"] = selection_error
    return payload


def api_connect_list() -> list[dict[str, Any]]:
    """Return list of connected platforms."""
    config = get_config()
    return list_platforms(config.platforms_path)


def api_connect(platform: str, path: str | None = None) -> dict[str, Any]:
    """Connect a platform and return result."""
    config = get_config()
    return connect_platform(config.platforms_path, platform, custom_path=path)


# ── Job queue management ─────────────────────────────────────────────


def api_retry_job(run_id: str) -> dict[str, Any]:
    """Retry a dead_letter job, returning result."""
    ok = retry_session_job(run_id)
    return {"retried": ok, "run_id": run_id, "queue": count_session_jobs_by_status()}


def api_skip_job(run_id: str) -> dict[str, Any]:
    """Skip a dead_letter job, returning result."""
    ok = skip_session_job(run_id)
    return {"skipped": ok, "run_id": run_id, "queue": count_session_jobs_by_status()}


def api_retry_all_dead_letter() -> dict[str, Any]:
    """Retry all dead_letter jobs across all projects."""
    dead = list_queue_jobs(status_filter="dead_letter")
    retried = 0
    for job in dead:
        rid = str(job.get("run_id") or "")
        if rid and retry_session_job(rid):
            retried += 1
    return {"retried": retried, "queue": count_session_jobs_by_status()}


def api_skip_all_dead_letter() -> dict[str, Any]:
    """Skip all dead_letter jobs across all projects."""
    dead = list_queue_jobs(status_filter="dead_letter")
    skipped = 0
    for job in dead:
        rid = str(job.get("run_id") or "")
        if rid and skip_session_job(rid):
            skipped += 1
    return {"skipped": skipped, "queue": count_session_jobs_by_status()}


def api_queue_jobs(
    status: str | None = None,
    project: str | None = None,
    project_like: str | None = None,
) -> dict[str, Any]:
    """List queue jobs with optional filters."""
    project_filter: str | None = None
    project_exact = False
    if project:
        config = get_config()
        try:
            selected = _resolve_selected_projects(
                config=config, scope="project", project=project
            )
        except ValueError as exc:
            # Backward-compatible fallback for HTTP/API callers that still
            # pass free-form project substrings.
            selected = []
            project_filter = str(project)
            project_exact = False
        if selected:
            _name, project_path = selected[0]
            project_filter = str(project_path)
            project_exact = True
    elif project_like:
        project_filter = project_like

    jobs = list_queue_jobs(
        status_filter=status,
        project_filter=project_filter,
        project_exact=project_exact,
        failed_only=(status == "failed"),
    )
    return {"jobs": jobs, "total": len(jobs), "queue": count_session_jobs_by_status()}


def api_unscoped(*, limit: int = 50) -> dict[str, Any]:
    """List unscoped indexed sessions and aggregate counts."""
    config = get_config()
    items = list_unscoped_sessions(projects=config.projects, limit=limit)
    counts = count_unscoped_sessions_by_agent(projects=config.projects)
    return {
        "items": items,
        "total": len(items),
        "count_by_agent": counts,
    }


# ── Project management ───────────────────────────────────────────────


def api_project_list() -> list[dict[str, Any]]:
    """Return registered projects from config."""
    config = get_config()
    result: list[dict[str, Any]] = []
    for name, path_str in config.projects.items():
        resolved = Path(path_str).expanduser().resolve()
        result.append(
            {
                "name": name,
                "path": str(resolved),
                "exists": resolved.exists(),
                "has_lerim": (resolved / ".lerim").is_dir(),
            }
        )
    return result


def api_project_add(path_str: str) -> dict[str, Any]:
    """Register a project directory and return status."""
    resolved = Path(path_str).expanduser().resolve()
    if not resolved.is_dir():
        return {"error": f"Not a directory: {resolved}", "name": None}

    name = resolved.name
    # Create canonical project memory layout under .lerim/
    lerim_dir = resolved / ".lerim"
    lerim_dir.mkdir(parents=True, exist_ok=True)
    memory_root = lerim_dir / "memory"
    _ensure_memory_root_layout(memory_root)

    # Update config
    save_config_patch({"projects": {name: str(resolved)}})

    return {
        "name": name,
        "path": str(resolved),
        "created_lerim_dir": True,
        "memory_dir": str(memory_root),
    }


def api_project_remove(name: str) -> dict[str, Any]:
    """Unregister a project by name."""
    config = get_config()
    if name not in config.projects:
        return {"error": f"Project not registered: {name}", "removed": False}

    existing: dict[str, Any] = {}
    if USER_CONFIG_PATH.exists():
        existing = load_toml_file(USER_CONFIG_PATH)

    projects = existing.get("projects", {})
    if isinstance(projects, dict) and name in projects:
        del projects[name]
        existing["projects"] = projects

    # Write directly — save_config_patch would re-merge the deleted key
    _write_config_full(existing)
    return {"name": name, "removed": True}


# ── Init wizard helpers ──────────────────────────────────────────────


def detect_agents() -> dict[str, dict[str, Any]]:
    """Detect available coding agents by checking known default paths."""
    result: dict[str, dict[str, Any]] = {}
    for name, default_path in AGENT_DEFAULT_PATHS.items():
        resolved = Path(default_path).expanduser()
        result[name] = {
            "path": str(resolved),
            "exists": resolved.exists(),
        }
    return result


def docker_available() -> bool:
    """Check if Docker is installed and the daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def write_init_config(selected_agents: dict[str, str]) -> Path:
    """Write initial [agents] config and return the config file path."""
    save_config_patch({"agents": selected_agents})
    return USER_CONFIG_PATH


# ── Docker management ────────────────────────────────────────────────


COMPOSE_PATH = Path.home() / ".lerim" / "docker-compose.yml"
GHCR_IMAGE = "ghcr.io/lerim-dev/lerim-cli"


_API_KEY_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "OPENCODE_API_KEY",
    "OPENROUTER_API_KEY",
    "ZAI_API_KEY",
)



def _find_package_root() -> Path | None:
    """Locate the Lerim source tree root by walking up from this file."""
    candidate = Path(__file__).resolve().parent
    for _ in range(5):
        if (candidate / "Dockerfile").is_file():
            return candidate
        candidate = candidate.parent
    return None


def _generate_compose_yml(build_local: bool = False) -> str:
    """Generate docker-compose.yml content from current config.

    When *build_local* is True the compose file uses a ``build:`` directive
    pointing at the local source tree (requires a Dockerfile).  Otherwise it
    references the pre-built GHCR image tagged with the current version.
    """
    config = reload_config()
    home = str(Path.home())

    # Mount only .lerim/ subdirectories — agent should NOT see source code.
    # Global lerim data (config, index, cache)
    lerim_dir = f"{home}/.lerim"
    volumes = [f"      - {lerim_dir}:{lerim_dir}"]

    # Agent session dirs (read-only — agent reads traces but never modifies them)
    for _name, path_str in config.agents.items():
        resolved = str(Path(path_str).expanduser().resolve())
        volumes.append(f"      - {resolved}:{resolved}:ro")

    # Project .lerim dirs only (NOT the whole project directory)
    for _name, path_str in config.projects.items():
        resolved = Path(path_str).expanduser().resolve()
        lerim_subdir = resolved / ".lerim"
        volumes.append(f"      - {lerim_subdir}:{lerim_subdir}")

    volumes_block = "\n".join(volumes)
    port = config.server_port

    # Forward API keys by name only — Docker reads values from host env.
    # NEVER write secret values into the compose file.
    env_lines = [
        f"      - HOME={home}",
        "      - FASTEMBED_CACHE_PATH=/opt/lerim/models",
    ]
    for key in _API_KEY_ENV_NAMES:
        if os.environ.get(key):
            env_lines.append(f"      - {key}")
    # Forward MLflow flag so tracing is enabled inside the container
    if os.environ.get("LERIM_MLFLOW"):
        env_lines.append("      - LERIM_MLFLOW")
    env_block = "\n".join(env_lines)

    if build_local:
        pkg_root = _find_package_root()
        if pkg_root is None:
            raise FileNotFoundError(
                "Cannot find Dockerfile in the Lerim source tree. "
                "Use 'lerim up' without --build to pull the GHCR image."
            )
        image_or_build = f"    build: {pkg_root}"
    else:
        image_or_build = f"    image: {GHCR_IMAGE}:{__version__}"

    # Set working_dir to first registered project's .lerim dir so
    # git_root_for() can work with the mounted .lerim subdirectory.
    workdir_line = ""
    if config.projects:
        first_project = next(iter(config.projects.values()))
        resolved_project = Path(first_project).expanduser().resolve()
        workdir_line = f'\n    working_dir: "{resolved_project / ".lerim"}"'

    # Resolve seccomp profile path (shipped with the package)
    seccomp_path = Path(__file__).parent / "lerim-seccomp.json"
    seccomp_line = ""
    if seccomp_path.exists():
        seccomp_line = f"\n      - seccomp={seccomp_path}"

    return f"""\
# Auto-generated by lerim up — do not edit manually.
# Regenerated from ~/.lerim/config.toml on every `lerim up`.
services:
  lerim:
{image_or_build}
    container_name: lerim{workdir_line}
    command: ["--host", "0.0.0.0", "--port", "{port}"]
    restart: "no"
    ports:
      - "127.0.0.1:{port}:{port}"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # Container hardening
    read_only: true
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true{seccomp_line}
    pids_limit: 256
    mem_limit: 2g
    tmpfs:
      - /tmp:size=100M
      - {home}/.codex:size=50M
      - {home}/.config:size=10M
      - /root/.codex:size=50M
      - /root/.config:size=10M
    environment:
{env_block}
    volumes:
{volumes_block}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
"""


def api_up(build_local: bool = False) -> dict[str, Any]:
    """Generate compose file and start Docker container.

    When *build_local* is True the image is built from the local Dockerfile
    instead of pulling the pre-built GHCR image.  Docker output is streamed
    to stderr in real-time so the user sees pull/build progress.
    """
    if not docker_available():
        return {"error": "Docker is not installed or not running."}

    try:
        compose_content = _generate_compose_yml(build_local=build_local)
    except FileNotFoundError as exc:
        return {"error": str(exc)}

    COMPOSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPOSE_PATH.write_text(compose_content, encoding="utf-8")
    # Owner-only read/write — compose file may reference secret key names.
    COMPOSE_PATH.chmod(0o600)

    cmd = ["docker", "compose", "-f", str(COMPOSE_PATH), "up", "-d"]
    if build_local:
        cmd.append("--build")

    try:
        result = subprocess.run(cmd, timeout=300)
    except subprocess.TimeoutExpired:
        return {"error": "Docker compose up timed out after 300 seconds."}
    if result.returncode != 0:
        return {"error": "docker compose up failed"}

    return {"status": "started", "compose_path": str(COMPOSE_PATH)}


def api_down() -> dict[str, Any]:
    """Stop Docker container. Reports whether it was actually running."""
    if not COMPOSE_PATH.exists():
        return {"status": "not_running", "message": "No compose file found."}

    was_running = is_container_running()

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_PATH), "down"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "docker compose down failed"}
    return {"status": "stopped", "was_running": was_running}


def is_container_running() -> bool:
    """Check if the Lerim Docker container API is reachable."""
    import urllib.request
    import urllib.error

    config = get_config()
    url = f"http://localhost:{config.server_port}/api/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


if __name__ == "__main__":
    health = api_health()
    assert health["status"] == "ok"
    assert "version" in health

    agents = detect_agents()
    assert isinstance(agents, dict)
    assert "claude" in agents

    docker_ok = docker_available()
    assert isinstance(docker_ok, bool)

    projects = api_project_list()
    assert isinstance(projects, list)

    print(f"api.py self-test passed: health={health}, docker={docker_ok}")
