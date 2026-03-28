"""Command-line interface for Lerim runtime, memory, and service operations.

Service commands (ask, sync, maintain, status) are thin HTTP clients that
talk to a running Lerim server (started via ``lerim up`` or ``lerim serve``).
Host-only commands (init, project, up, down, logs, connect, memory)
run locally and never require an HTTP server.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal, cast

from lerim import __version__
from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    connect_platform,
    list_platforms,
    load_platforms,
    remove_platform,
)

from lerim.app.api import (
    api_project_add,
    api_project_list,
    api_project_remove,
    api_up,
    api_down,
    detect_agents,
    docker_available,
    is_container_running,
    write_init_config,
)
from lerim.app.arg_utils import parse_csv
from lerim.app.daemon import (
    run_maintain_once,
    run_sync_once,
    resolve_window_bounds,
)
from lerim.config.project_scope import resolve_data_dirs
from lerim.config.logging import configure_logging
from lerim.app.auth import cmd_auth, cmd_auth_logout, cmd_auth_status
from lerim.config.settings import get_config, USER_CONFIG_PATH
from lerim.config.tracing import configure_tracing
from lerim.memory.memory_repo import build_memory_paths, reset_memory_root
from lerim.memory.memory_record import (
    MemoryRecord,
    MemoryType,
    canonical_memory_filename,
    memory_folder,
    slugify,
)


def _emit(message: object = "", *, file: Any | None = None) -> None:
    """Write one CLI output line to stdout or a provided file-like target."""
    target = file if file is not None else sys.stdout
    target.write(f"{message}\n")


def _emit_structured(*, title: str, payload: dict[str, Any], as_json: bool) -> None:
    """Emit a dict payload either as JSON or as key/value lines."""
    if as_json:
        _emit(json.dumps(payload, indent=2, ensure_ascii=True))
        return
    _emit(title)
    for key, value in payload.items():
        _emit(f"- {key}: {value}")


def _not_running() -> int:
    """Print an error that the Lerim server is not reachable and return exit 1."""
    _emit(
        "Lerim is not running. Start with: lerim up (Docker) or lerim serve (direct)",
        file=sys.stderr,
    )
    return 1


def _wait_for_ready(port: int, timeout: int = 30) -> bool:
	"""Poll /api/health until the server responds or *timeout* seconds elapse."""
	url = f"http://localhost:{port}/api/health"
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		try:
			req = urllib.request.Request(url, method="GET")
			with urllib.request.urlopen(req, timeout=5) as resp:
				if resp.status == 200:
					return True
		except (urllib.error.URLError, OSError):
			pass
		time.sleep(1)
	return False


def _api_get(path: str) -> dict[str, Any] | None:
    """GET from the running Lerim server. Returns None if not reachable."""
    config = get_config()
    url = f"http://localhost:{config.server_port}{path}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _api_post(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """POST JSON to the running Lerim server. Returns None if not reachable."""
    config = get_config()
    url = f"http://localhost:{config.server_port}{path}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _hoist_global_json_flag(raw: list[str]) -> list[str]:
    """Allow ``--json`` before or after subcommands by normalizing argv order."""
    if "--json" not in raw:
        return raw
    return ["--json"] + [item for item in raw if item != "--json"]


def _cmd_connect(args: argparse.Namespace) -> int:
    """Handle ``lerim connect`` actions (list/auto/remove/connect)."""
    config = get_config()
    platforms_path = config.platforms_path
    action = getattr(args, "platform_name", None)

    if action == "list" or action is None:
        entries = list_platforms(platforms_path)
        if not entries:
            _emit("No platforms connected.")
            return 0
        _emit(f"Connected platforms: {len(entries)}")
        for entry in entries:
            status = "ok" if entry["exists"] else "missing"
            _emit(
                f"- {entry['name']}: {entry['path']} ({entry['session_count']} sessions, {status})"
            )
        return 0

    if action == "auto":
        connected = 0
        for name in KNOWN_PLATFORMS:
            result = connect_platform(platforms_path, name, custom_path=None)
            if result.get("status") == "connected":
                connected += 1
        _emit(f"Auto connected: {connected}")
        return 0

    if action == "remove":
        name = getattr(args, "extra_arg", None)
        if not name:
            _emit("Usage: lerim connect remove <platform>", file=sys.stderr)
            return 2
        removed = remove_platform(platforms_path, name)
        _emit(f"Removed: {name}" if removed else f"Platform not connected: {name}")
        return 0

    name = action
    if name not in KNOWN_PLATFORMS:
        _emit(f"Unknown platform: {name}", file=sys.stderr)
        _emit(f"Known platforms: {', '.join(KNOWN_PLATFORMS)}", file=sys.stderr)
        return 2

    existing = load_platforms(platforms_path)
    existing_path = (existing.get("platforms", {}).get(name) or {}).get("path")
    result = connect_platform(
        platforms_path, name, custom_path=getattr(args, "path", None)
    )
    status = str(result.get("status") or "")
    if status == "path_not_found":
        _emit(f"Path not found: {result.get('path')}", file=sys.stderr)
        return 1
    if status == "unknown_platform":
        _emit(f"Unknown platform: {name}", file=sys.stderr)
        return 1

    _emit(f"Connected: {name}")
    _emit(f"- Path: {result.get('path')}")
    _emit(f"- Sessions: {result.get('session_count')}")
    if existing_path and existing_path == result.get("path"):
        _emit("- Path unchanged, no initial reindex trigger.")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    """Forward sync request to the running Lerim server."""
    body: dict[str, Any] = {
        "agent": getattr(args, "agent", None),
        "window": getattr(args, "window", None),
        "max_sessions": getattr(args, "max_sessions", None),
        "force": getattr(args, "force", False),
        "dry_run": getattr(args, "dry_run", False),
    }
    data = _api_post("/api/sync", body)
    if data is None:
        return _not_running()
    _emit_structured(title="Sync:", payload=data, as_json=args.json)
    return 0


def _cmd_maintain(args: argparse.Namespace) -> int:
    """Forward maintain request to the running Lerim server."""
    body = {
        "force": getattr(args, "force", False),
        "dry_run": getattr(args, "dry_run", False),
    }
    data = _api_post("/api/maintain", body)
    if data is None:
        return _not_running()
    _emit_structured(title="Maintain:", payload=data, as_json=args.json)
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
	"""Show dashboard transition message."""
	print()
	print("  Lerim Dashboard is moving to the cloud.")
	print("  The new dashboard will be available at https://lerim.dev")
	print()
	print("  In the meantime, use these CLI commands:")
	print("    lerim status     - system overview")
	print("    lerim ask        - query your memories")
	print("    lerim queue      - view session processing queue")
	print("    lerim sync       - process new sessions")
	print("    lerim maintain   - run memory maintenance")
	print()
	return 0


def _cmd_memory_search(args: argparse.Namespace) -> int:
    """Search memory files via rg and print matching lines."""
    import subprocess

    config = get_config()
    memory_dir = config.memory_dir
    if not memory_dir.exists():
        _emit("No matching memories.")
        return 0
    cmd = ["rg", "--ignore-case", "--glob=*.md", args.query, str(memory_dir)]
    if args.json:
        cmd.insert(1, "--json")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if result.stdout:
        _emit(result.stdout.strip())
    else:
        _emit("No matching memories.")
    return 0


def _cmd_memory_list(args: argparse.Namespace) -> int:
    """List memory files in the memory directory."""
    config = get_config()
    memory_dir = config.memory_dir
    if not memory_dir.exists():
        return 0
    files = sorted(memory_dir.rglob("*.md"))[: args.limit]
    if args.json:
        _emit(json.dumps([str(f) for f in files], indent=2))
        return 0
    for f in files:
        _emit(str(f))
    return 0


def _cmd_memory_add(args: argparse.Namespace) -> int:
    """Add one memory item from CLI flags."""
    config = get_config()
    primitive = MemoryType(str(args.primitive or "learning"))
    if primitive == MemoryType.summary:
        _emit("Primitive 'summary' is not allowed in memory add", file=sys.stderr)
        return 2
    primitive_value = cast(
        Literal["decision", "learning"],
        "decision" if primitive == MemoryType.decision else "learning",
    )
    kind = str(args.kind or "insight")
    record = MemoryRecord(
        id=slugify(args.title),
        primitive=primitive_value,
        kind=kind,
        title=args.title,
        body=args.body,
        confidence=args.confidence,
        tags=parse_csv(args.tags),
        source="cli",
    )
    folder = config.memory_dir / memory_folder(primitive)
    folder.mkdir(parents=True, exist_ok=True)
    filename = canonical_memory_filename(title=args.title, run_id="cli")
    filepath = folder / filename
    filepath.write_text(record.to_markdown(), encoding="utf-8")
    _emit(f"Added memory: {record.id} -> {filepath}")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    """Forward ask query to the running Lerim server."""
    data = _api_post("/api/ask", {"question": args.question, "limit": args.limit})
    if data is None:
        return _not_running()
    if data.get("error"):
        _emit(data.get("answer", "Error"), file=sys.stderr)
        return 1
    if args.json:
        _emit(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        _emit(data.get("answer", ""))
    return 0


def _cmd_memory_reset(args: argparse.Namespace) -> int:
    """Reset memory/index trees for project/global roots."""
    if not args.yes:
        _emit("Refusing to reset without --yes", file=sys.stderr)
        return 2
    config = get_config()
    resolved = resolve_data_dirs(
        scope=config.memory_scope,
        project_dir_name=config.memory_project_dir_name,
        global_data_dir=config.global_data_dir or config.data_dir,
        repo_path=Path.cwd(),
    )
    targets: list[Path] = []
    selected_scope = str(args.scope or "both")
    if selected_scope in {"project", "both"} and resolved.project_data_dir:
        targets.append(resolved.project_data_dir)
    if selected_scope in {"global", "both"}:
        targets.append(resolved.global_data_dir)

    seen: set[Path] = set()
    summaries: list[dict[str, Any]] = []
    for data_root in targets:
        root = data_root.resolve()
        if root in seen:
            continue
        seen.add(root)
        layout = build_memory_paths(root)
        result = reset_memory_root(layout)
        summaries.append(
            {"data_dir": str(root), "removed": result.get("removed") or []}
        )

    if args.json:
        _emit(json.dumps({"reset": summaries}, indent=2, ensure_ascii=True))
    else:
        _emit("Memory reset completed:")
        for item in summaries:
            _emit(f"- {item['data_dir']}: removed={len(item['removed'])}")
    return 0


def _relative_time(iso_str: str) -> str:
	"""Convert an ISO timestamp to a human-readable relative string."""
	try:
		from datetime import datetime, timezone
		dt = datetime.fromisoformat(iso_str)
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		delta = datetime.now(timezone.utc) - dt
		secs = int(delta.total_seconds())
		if secs < 0:
			return "just now"
		if secs < 60:
			return f"{secs}s ago"
		if secs < 3600:
			return f"{secs // 60}m ago"
		if secs < 86400:
			return f"{secs // 3600}h ago"
		return f"{secs // 86400}d ago"
	except (ValueError, TypeError):
		return "?"


def _format_queue_counts(counts: dict[str, int]) -> str:
	"""Format queue status counts into a summary string."""
	order = ["pending", "running", "done", "failed", "dead_letter"]
	parts = []
	for status in order:
		n = counts.get(status, 0)
		if n > 0:
			parts.append(f"{n} {status}")
	return ", ".join(parts) if parts else "empty"


def _resolve_project_repo_path(name: str) -> str | None:
	"""Resolve a project name to its repo_path."""
	config = get_config()
	# Exact match first
	if name in config.projects:
		return str(Path(config.projects[name]).expanduser().resolve())
	# Substring match
	for pname, ppath in config.projects.items():
		if name in pname:
			return str(Path(ppath).expanduser().resolve())
	return None


def _cmd_queue(args: argparse.Namespace) -> int:
	"""Display the session extraction queue."""
	from lerim.sessions.catalog import list_queue_jobs, count_session_jobs_by_status

	jobs = list_queue_jobs(
		status_filter=getattr(args, "status", None),
		project_filter=getattr(args, "project", None),
		failed_only=getattr(args, "failed", False),
	)
	counts = count_session_jobs_by_status()

	if args.json:
		_emit(json.dumps({"jobs": jobs, "total": len(jobs), "queue": counts}, indent=2, default=str))
		return 0

	if not jobs:
		_emit("Session Queue: no jobs")
		_emit(_format_queue_counts(counts))
		return 0

	from rich.console import Console
	from rich.table import Table

	table = Table(title=f"Session Queue ({len(jobs)} jobs)")
	table.add_column("STATUS", style="bold")
	table.add_column("RUN ID")
	table.add_column("PROJECT")
	table.add_column("AGENT")
	table.add_column("AGE")
	table.add_column("ERROR")

	status_styles = {
		"pending": "dim white", "running": "cyan",
		"failed": "yellow", "dead_letter": "red bold",
		"done": "green",
	}

	for job in jobs:
		st = str(job.get("status") or "")
		style = status_styles.get(st, "")
		rid = str(job.get("run_id") or "")[:8]
		rp = str(job.get("repo_path") or "")
		proj = Path(rp).name if rp else ""
		agent = str(job.get("agent_type") or "")
		age = _relative_time(str(job.get("updated_at") or ""))
		err = str(job.get("error") or "")[:50]
		table.add_row(f"[{style}]{st}[/{style}]", rid, proj, agent, age, err)

	Console().print(table)
	_emit(_format_queue_counts(counts))
	if counts.get("dead_letter", 0) > 0:
		_emit("Retry: lerim retry <run_id>  |  Retry all: lerim retry --all")
	return 0


def _cmd_retry(args: argparse.Namespace) -> int:
	"""Retry dead_letter jobs."""
	from lerim.sessions.catalog import (
		retry_session_job, retry_project_jobs,
		resolve_run_id_prefix, list_queue_jobs,
		count_session_jobs_by_status,
	)

	run_id = getattr(args, "run_id", None)
	project = getattr(args, "project", None)
	retry_all = getattr(args, "all", False)

	if retry_all:
		dead = list_queue_jobs(status_filter="dead_letter")
		if not dead:
			_emit("No dead_letter jobs to retry.")
			return 0
		count = 0
		for job in dead:
			if retry_session_job(str(job["run_id"])):
				count += 1
		_emit(f"Retried {count} dead_letter job(s).")
		_emit(_format_queue_counts(count_session_jobs_by_status()))
		return 0

	if project:
		repo_path = _resolve_project_repo_path(project)
		if not repo_path:
			_emit(f"Project not found: {project}", file=sys.stderr)
			return 1
		count = retry_project_jobs(repo_path)
		_emit(f"Retried {count} dead_letter job(s) for project {project}.")
		_emit(_format_queue_counts(count_session_jobs_by_status()))
		return 0

	if not run_id:
		_emit("Provide a run_id, --project, or --all.", file=sys.stderr)
		return 2

	if len(run_id) < 6:
		_emit("Run ID prefix must be at least 6 characters.", file=sys.stderr)
		return 2

	full_id = resolve_run_id_prefix(run_id)
	if not full_id:
		_emit(f"Run ID not found or ambiguous: {run_id}", file=sys.stderr)
		return 1

	ok = retry_session_job(full_id)
	if ok:
		_emit(f"Retried: {run_id}")
		_emit(_format_queue_counts(count_session_jobs_by_status()))
	else:
		_emit(f"Job {run_id} is not in dead_letter status.", file=sys.stderr)
		return 1
	return 0


def _cmd_skip(args: argparse.Namespace) -> int:
	"""Skip dead_letter jobs."""
	from lerim.sessions.catalog import (
		skip_session_job, skip_project_jobs,
		resolve_run_id_prefix, list_queue_jobs,
		count_session_jobs_by_status,
	)

	run_id = getattr(args, "run_id", None)
	project = getattr(args, "project", None)
	skip_all = getattr(args, "all", False)

	if skip_all:
		dead = list_queue_jobs(status_filter="dead_letter")
		if not dead:
			_emit("No dead_letter jobs to skip.")
			return 0
		count = 0
		for job in dead:
			if skip_session_job(str(job["run_id"])):
				count += 1
		_emit(f"Skipped {count} dead_letter job(s).")
		_emit(_format_queue_counts(count_session_jobs_by_status()))
		return 0

	if project:
		repo_path = _resolve_project_repo_path(project)
		if not repo_path:
			_emit(f"Project not found: {project}", file=sys.stderr)
			return 1
		count = skip_project_jobs(repo_path)
		_emit(f"Skipped {count} dead_letter job(s) for project {project}.")
		_emit(_format_queue_counts(count_session_jobs_by_status()))
		return 0

	if not run_id:
		_emit("Provide a run_id, --project, or --all.", file=sys.stderr)
		return 2

	if len(run_id) < 6:
		_emit("Run ID prefix must be at least 6 characters.", file=sys.stderr)
		return 2

	full_id = resolve_run_id_prefix(run_id)
	if not full_id:
		_emit(f"Run ID not found or ambiguous: {run_id}", file=sys.stderr)
		return 1

	ok = skip_session_job(full_id)
	if ok:
		_emit(f"Skipped: {run_id} -> done")
		_emit(_format_queue_counts(count_session_jobs_by_status()))
	else:
		_emit(f"Job {run_id} is not in dead_letter status.", file=sys.stderr)
		return 1
	return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Forward status request to the running Lerim server."""
    data = _api_get("/api/status")
    if data is None:
        return _not_running()
    if args.json:
        _emit(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        _emit("Lerim status:")
        _emit(f"- connected_agents: {len(data.get('connected_agents', []))}")
        _emit(f"- memory_count: {data.get('memory_count', 0)}")
        _emit(f"- sessions_indexed_count: {data.get('sessions_indexed_count', 0)}")
        queue = data.get("queue", {})
        _emit(f"- queue: {_format_queue_counts(queue)}")
        dl = queue.get("dead_letter", 0)
        if dl > 0:
            _emit(f"  ! {dl} dead_letter job(s). Run: lerim queue --failed")
    return 0


_PROVIDERS = [
    ("opencode_go", "OPENCODE_API_KEY", "OpenCode Go", "Free tier available — opencode.ai"),
    ("openrouter", "OPENROUTER_API_KEY", "OpenRouter", "Access 100+ models — openrouter.ai"),
    ("openai", "OPENAI_API_KEY", "OpenAI", "GPT models — platform.openai.com"),
    ("minimax", "MINIMAX_API_KEY", "MiniMax", "MiniMax models — minimax.io"),
    ("zai", "ZAI_API_KEY", "Z.AI", "GLM models — z.ai"),
    ("anthropic", "ANTHROPIC_API_KEY", "Anthropic", "Claude models — anthropic.com"),
    ("ollama", "", "Ollama", "Local models — no API key needed"),
]


def _setup_api_keys() -> None:
    """Interactive API key setup — saves to ~/.lerim/.env."""
    env_path = Path.home() / ".lerim" / ".env"

    # Load existing keys if any
    existing: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()

    _emit("\n── LLM Provider Setup ──────────────────────────────")
    _emit("")
    _emit("  Lerim needs an LLM provider to extract memories from your sessions.")
    _emit("  Select your provider(s) and enter API keys. You can change these")
    _emit("  later in ~/.lerim/.env and ~/.lerim/config.toml.")
    _emit("")
    _emit("  Available providers:")
    _emit("")
    for i, (pid, env_var, name, desc) in enumerate(_PROVIDERS, 1):
        has_key = "✓" if existing.get(env_var) else " "
        _emit(f"  [{has_key}] {i}. {name:<14} {desc}")
    _emit("")

    answer = input("  Enter provider numbers (comma-separated, e.g. 1,3) or press Enter to skip: ").strip()
    if not answer:
        if existing:
            _emit(f"  Keeping existing keys in {env_path}")
        else:
            _emit("  Skipped. Set API keys later in ~/.lerim/.env")
        return

    # Parse selections
    new_keys: dict[str, str] = dict(existing)  # preserve existing
    try:
        indices = [int(x.strip()) - 1 for x in answer.split(",") if x.strip()]
    except ValueError:
        _emit("  Invalid input. Skipping API key setup.")
        return

    _emit("")
    for idx in indices:
        if idx < 0 or idx >= len(_PROVIDERS):
            continue
        pid, env_var, name, desc = _PROVIDERS[idx]
        if not env_var:
            _emit(f"  {name}: no API key needed (local provider)")
            continue

        current = existing.get(env_var, "")
        masked = f" (current: ...{current[-8:]})" if current else ""
        key = input(f"  {name} API key{masked}: ").strip()
        if key:
            new_keys[env_var] = key
        elif current:
            _emit("    Keeping existing key")

    # Write ~/.lerim/.env
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Lerim API keys — managed by `lerim init`", ""]
    for k, v in sorted(new_keys.items()):
        lines.append(f"{k}={v}")
    lines.append("")
    env_path.write_text("\n".join(lines), encoding="utf-8")
    env_path.chmod(0o600)  # restrict permissions — secrets file
    _emit(f"\n  Keys saved to {env_path} (permissions: 600)")


def _cmd_init(args: argparse.Namespace) -> int:
    """Interactive setup wizard — agents, API keys, config."""
    _emit("")
    _emit("  ╔═══════════════════════════════════╗")
    _emit("  ║       Welcome to Lerim            ║")
    _emit("  ╚═══════════════════════════════════╝")

    # Step 1: Detect coding agents
    _emit("\n── Coding Agents ──────────────────────────────────")
    _emit("")
    _emit("  Which coding agents do you use?")
    _emit("")

    detected = detect_agents()
    selected: dict[str, str] = {}
    for name, info in detected.items():
        exists = info["exists"]
        marker = "detected" if exists else "not found"
        answer = (
            input(f"  {name} ({marker}) [{'Y/n' if exists else 'y/N'}]: ")
            .strip()
            .lower()
        )
        if (exists and answer != "n") or (not exists and answer == "y"):
            selected[name] = info["path"]

    if selected:
        write_init_config(selected)
        _emit(f"\n  Config written to {USER_CONFIG_PATH}")
        _emit(f"  Agents: {', '.join(selected.keys())}")
    else:
        _emit("\n  No agents selected. Add them later in:")
        _emit(f"  {USER_CONFIG_PATH}")

    # Step 2: API keys
    _setup_api_keys()

    # Step 3: Docker check
    _emit("\n── Docker ─────────────────────────────────────────")
    _emit("")
    if docker_available():
        _emit("  Docker: found ✓")
    else:
        _emit("  Docker: not found")
        _emit("  Install Docker to use `lerim up` (recommended).")
        _emit("  Or run `lerim serve` directly without Docker.")

    # Done
    _emit("\n── Next Steps ─────────────────────────────────────")
    _emit("")
    _emit("  1. lerim project add /path/to/repo   # register a project")
    _emit("  2. lerim up                           # start the service")
    _emit("")
    _emit("  Change providers:  ~/.lerim/config.toml")
    _emit("  Change API keys:   ~/.lerim/.env")
    _emit("")
    return 0


def _cmd_project(args: argparse.Namespace) -> int:
    """Dispatch project subcommands."""
    action = getattr(args, "project_action", None)
    if not action:
        _emit("Usage: lerim project {add,list,remove}", file=sys.stderr)
        return 2

    if action == "list":
        projects = api_project_list()
        if args.json:
            _emit(json.dumps(projects, indent=2, ensure_ascii=True))
            return 0
        if not projects:
            _emit("No projects registered.")
            return 0
        _emit(f"Registered projects: {len(projects)}")
        for p in projects:
            status = "ok" if p["exists"] else "missing"
            lerim = " .lerim/" if p["has_lerim"] else ""
            _emit(f"  {p['name']}: {p['path']} ({status}{lerim})")
        return 0

    if action == "add":
        path_str = getattr(args, "path", None)
        if not path_str:
            _emit("Usage: lerim project add <path>", file=sys.stderr)
            return 2
        result = api_project_add(path_str)
        if result.get("error"):
            _emit(result["error"], file=sys.stderr)
            return 1
        _emit(f'Added project "{result["name"]}" ({result["path"]})')
        _emit(f"Created {result['path']}/.lerim/")
        # Restart container if running
        if is_container_running():
            _emit("Restarting Lerim to mount new project...")
            api_up()
            _emit("Done.")
        return 0

    if action == "remove":
        name = getattr(args, "name", None)
        if not name:
            _emit("Usage: lerim project remove <name>", file=sys.stderr)
            return 2
        result = api_project_remove(name)
        if result.get("error"):
            _emit(result["error"], file=sys.stderr)
            return 1
        _emit(f'Removed project "{name}"')
        if is_container_running():
            _emit("Restarting Lerim...")
            api_up()
            _emit("Done.")
        return 0

    _emit("Usage: lerim project {add,list,remove}", file=sys.stderr)
    return 2


def _cmd_up(args: argparse.Namespace) -> int:
	"""Start the Docker container."""
	config = get_config()
	_emit(
		f"Starting Lerim with {len(config.projects)} projects and {len(config.agents)} agents..."
	)
	result = api_up(build_local=getattr(args, "build", False))
	if result.get("error"):
		_emit(result["error"], file=sys.stderr)
		return 1

	if not _wait_for_ready(config.server_port):
		_emit(
			"Container started but the server is not responding. "
			"Check logs with: lerim logs",
			file=sys.stderr,
		)
		return 1

	_emit(f"Lerim is running at http://localhost:{config.server_port}")
	return 0


def _cmd_down(args: argparse.Namespace) -> int:
    """Stop the Docker container."""
    result = api_down()
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return 1
    if result.get("status") == "not_running":
        _emit("Lerim is not running.")
        return 0
    if result.get("was_running"):
        _emit("Lerim stopped.")
    else:
        _emit("Lerim was not running. Cleaned up containers.")
    return 0


def _parse_since(since: str) -> float:
    """Parse a relative duration string (e.g. ``1h``, ``30m``, ``2d``) into seconds."""
    import re

    m = re.fullmatch(r"(\d+)\s*([smhd])", since.strip().lower())
    if not m:
        raise ValueError(f"Invalid --since format: {since!r}  (expected e.g. 1h, 30m, 2d)")
    value, unit = int(m.group(1)), m.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers[unit]


def _fmt_log_line(entry: dict[str, Any], *, color: bool) -> str:
    """Format a parsed JSONL log entry for terminal display."""
    ts_raw = str(entry.get("ts") or "")
    # Extract HH:MM:SS from ISO-8601 timestamp
    hms = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
    level = str(entry.get("level") or "").upper()
    message = str(entry.get("message") or "")

    if not color:
        return f"{hms} | {level:<8} | {message}"

    # ANSI colour codes for log levels
    _LEVEL_COLORS: dict[str, str] = {
        "TRACE": "\033[37m",     # white/grey
        "DEBUG": "\033[36m",     # cyan
        "INFO": "\033[32m",      # green
        "SUCCESS": "\033[1;32m", # bold green
        "WARNING": "\033[33m",   # yellow
        "ERROR": "\033[31m",     # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"
    clr = _LEVEL_COLORS.get(level, "")
    return f"\033[32m{hms}\033[0m | {clr}{level:<8}{_RESET} | {clr}{message}{_RESET}"


def _cmd_logs(args: argparse.Namespace) -> int:
    """Read and display local JSONL log entries from ``~/.lerim/logs/lerim.jsonl``."""
    from lerim.config.logging import LOG_DIR

    jsonl_path = LOG_DIR / "lerim.jsonl"

    if not jsonl_path.exists():
        _emit("No log file found. Logs will appear after Lerim runs.", file=sys.stderr)
        return 1

    is_tty = sys.stdout.isatty()
    raw_json = getattr(args, "raw_json", False) or getattr(args, "json", False)
    level_filter = (getattr(args, "level", None) or "").upper() or None
    since_str = getattr(args, "since", None)
    follow = getattr(args, "follow", False)

    # Compute cutoff timestamp for --since
    cutoff_ts: float | None = None
    if since_str:
        import datetime as _dt

        cutoff_ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=_parse_since(since_str))).timestamp()

    def _matches(entry: dict[str, Any]) -> bool:
        """Return True if the entry passes level and time filters."""
        if level_filter and str(entry.get("level") or "").upper() != level_filter:
            return False
        if cutoff_ts is not None:
            from datetime import datetime, timezone

            ts_raw = str(entry.get("ts") or "")
            try:
                entry_dt = datetime.fromisoformat(ts_raw)
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=timezone.utc)
                if entry_dt.timestamp() < cutoff_ts:
                    return False
            except (ValueError, TypeError):
                return False
        return True

    def _print_entry(entry: dict[str, Any]) -> None:
        if raw_json:
            _emit(json.dumps(entry, ensure_ascii=True, default=str))
        else:
            _emit(_fmt_log_line(entry, color=is_tty))

    if follow:
        # Live tail: seek to end, then poll for new lines
        try:
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                fh.seek(0, 2)  # seek to end
                while True:
                    line = fh.readline()
                    if not line:
                        time.sleep(0.25)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if _matches(entry):
                        _print_entry(entry)
        except KeyboardInterrupt:
            pass
        return 0

    # Non-follow: read last N matching lines
    limit = 50
    matching: list[dict[str, Any]] = []
    try:
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if _matches(entry):
                    matching.append(entry)
    except OSError as exc:
        _emit(f"Error reading log file: {exc}", file=sys.stderr)
        return 1

    # Show only the last `limit` entries
    for entry in matching[-limit:]:
        _print_entry(entry)

    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start HTTP API + daemon loop in one process (web UI is Lerim Cloud)."""
    import signal
    import threading
    from http.server import ThreadingHTTPServer

    from lerim.app.dashboard import DashboardHandler
    from lerim.sessions.catalog import init_sessions_db

    config = get_config()
    host = args.host or config.server_host or "0.0.0.0"
    port = int(args.port or config.server_port or 8765)

    init_sessions_db()
    httpd = ThreadingHTTPServer((host, port), DashboardHandler)
    httpd.timeout = 1.0

    stop_event = threading.Event()

    def _daemon_loop() -> None:
        """Background daemon loop with independent sync and maintain intervals."""
        from lerim.app.arg_utils import parse_duration_to_seconds as _parse_dur
        from lerim.config.logging import logger
        from lerim.runtime.ollama_lifecycle import ollama_lifecycle

        sync_interval = max(config.sync_interval_minutes * 60, 30)
        maintain_interval = max(config.maintain_interval_minutes * 60, 30)

        # Initialise to (now - interval) so both trigger on the first
        # iteration regardless of the monotonic clock epoch.  In Docker
        # containers the monotonic clock reflects VM uptime which can be
        # smaller than maintain_interval, causing the first maintain to
        # be silently skipped when initialised to 0.0.
        _now_init = time.monotonic()
        last_sync = _now_init - sync_interval
        last_maintain = _now_init - maintain_interval

        logger.info(
            "daemon loop started (sync every {}s, maintain every {}s)",
            sync_interval,
            maintain_interval,
        )

        while not stop_event.is_set():
            now = time.monotonic()

            if now - last_sync >= sync_interval:
                try:
                    window_start, window_end = resolve_window_bounds(
                        window=f"{config.sync_window_days}d",
                        since_raw=None,
                        until_raw=None,
                        parse_duration_to_seconds=_parse_dur,
                    )
                    with ollama_lifecycle(config):
                        _code, summary = run_sync_once(
                            run_id=None,
                            agent_filter=None,
                            no_extract=False,
                            force=False,
                            max_sessions=config.sync_max_sessions,
                            dry_run=False,
                            ignore_lock=False,
                            trigger="daemon",
                            window_start=window_start,
                            window_end=window_end,
                        )
                    logger.info(
                        "daemon sync done — indexed={} extracted={} skipped={} failed={}",
                        summary.indexed_sessions,
                        summary.extracted_sessions,
                        summary.skipped_sessions,
                        summary.failed_sessions,
                    )
                except Exception as exc:
                    logger.warning("daemon sync error: {}", exc)
                last_sync = time.monotonic()

            if now - last_maintain >= maintain_interval:
                try:
                    with ollama_lifecycle(config):
                        _code, details = run_maintain_once(
                            force=False,
                            dry_run=False,
                            trigger="daemon",
                        )
                    logger.info("daemon maintain done — {}", details)
                except Exception as exc:
                    logger.warning("daemon maintain error: {}", exc)
                last_maintain = time.monotonic()

            # Ship to cloud (best-effort)
            if config.cloud_token:
                try:
                    from lerim.app.cloud_shipper import ship_once
                    import asyncio
                    results = asyncio.run(ship_once(config))
                    if results:
                        logger.info("cloud sync: {}", results)
                except Exception as exc:
                    logger.warning("cloud sync error: {}", exc)

            next_sync = last_sync + sync_interval
            next_maintain = last_maintain + maintain_interval
            sleep_for = max(1.0, min(next_sync, next_maintain) - time.monotonic())
            stop_event.wait(sleep_for)

    daemon_thread = threading.Thread(
        target=_daemon_loop, name="lerim-daemon", daemon=True
    )
    daemon_thread.start()

    def _shutdown(_signum: int, _frame: Any) -> None:
        """Signal handler — just set the stop flag (no lock-acquiring calls)."""
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    from lerim.config.logging import logger

    logger.info("Lerim serve running at http://{}:{}/", host, port)
    while not stop_event.is_set():
        httpd.handle_request()
    httpd.server_close()
    daemon_thread.join(timeout=5)
    return 0


_SKILL_TARGETS: dict[str, Path] = {
    "agents": Path.home() / ".agents" / "skills" / "lerim",
    "claude": Path.home() / ".claude" / "skills" / "lerim",
}
"""Skill install targets: ~/.agents/skills (shared by most agents) + ~/.claude/skills (Claude-specific)."""


def _cmd_skill(args: argparse.Namespace) -> int:
    """Install Lerim skill files into coding agent directories."""
    action = getattr(args, "skill_action", None)
    if action != "install":
        _emit("Usage: lerim skill install")
        return 2

    from lerim.skills import SKILLS_DIR

    skill_files = [SKILLS_DIR / "SKILL.md", SKILLS_DIR / "cli-reference.md"]
    missing = [f for f in skill_files if not f.exists()]
    if missing:
        _emit(f"Skill files not found in package: {missing}", file=sys.stderr)
        return 1

    installed = []
    for label, dest in _SKILL_TARGETS.items():
        dest.mkdir(parents=True, exist_ok=True)
        for src in skill_files:
            (dest / src.name).write_text(src.read_text())
        installed.append(
            f"~/.{label}/skills/lerim"
            if label != "agents"
            else "~/.agents/skills/lerim"
        )

    _emit(f"Installed lerim skill to: {', '.join(installed)}")
    _emit("  ~/.agents/skills/lerim  → Cursor, Codex, OpenCode, and others")
    _emit("  ~/.claude/skills/lerim  → Claude Code")
    return 0


def _cmd_auth_dispatch(args: argparse.Namespace) -> int:
    """Dispatch auth subcommands to the appropriate handler."""
    auth_command = getattr(args, "auth_command", None)
    if auth_command == "status":
        return cmd_auth_status(args)
    if auth_command == "logout":
        return cmd_auth_logout(args)
    # Default: login (bare `lerim auth` or `lerim auth login`)
    return cmd_auth(args)


def build_parser() -> argparse.ArgumentParser:
    """Construct the canonical Lerim command-line parser."""
    _F = argparse.RawDescriptionHelpFormatter  # noqa: N806
    parser = argparse.ArgumentParser(
        prog="lerim",
        formatter_class=_F,
        description="Lerim -- continual learning layer for coding agents.\n"
        "Indexes agent sessions, extracts memories, and answers questions\n"
        "using accumulated project knowledge.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of human-readable text (works with status, memory list, etc.)",
    )
    sub = parser.add_subparsers(dest="command")

    # ── connect ──────────────────────────────────────────────────────
    connect = sub.add_parser(
        "connect",
        formatter_class=_F,
        help="Manage connected agent platforms",
        description=(
            "Register, list, or remove agent platform connections.\n"
            "Lerim reads session data from connected platforms to build memory.\n\n"
            "Supported platforms: claude, codex, cursor, opencode\n\n"
            "Examples:\n"
            "  lerim connect list              # show all connected platforms\n"
            "  lerim connect auto              # auto-detect and connect all known platforms\n"
            "  lerim connect claude             # connect the Claude platform\n"
            "  lerim connect claude --path /custom/sessions\n"
            "  lerim connect remove claude      # disconnect Claude"
        ),
    )
    connect.add_argument(
        "platform_name",
        nargs="?",
        help="Action or platform name: 'list' (show connections), 'auto' (connect all detected), "
        "'remove' (disconnect, needs extra_arg), or a platform name to connect",
    )
    connect.add_argument(
        "extra_arg",
        nargs="?",
        help="Used with 'remove' action -- the platform name to disconnect (e.g. lerim connect remove claude)",
    )
    connect.add_argument(
        "--path",
        help="Custom filesystem path to the platform's session store (overrides auto-detected path)",
    )
    connect.set_defaults(func=_cmd_connect)

    # ── sync ─────────────────────────────────────────────────────────
    sync = sub.add_parser(
        "sync",
        formatter_class=_F,
        help="Index new sessions and extract memories (hot path)",
        description=(
            "Hot-path: discover new agent sessions from connected platforms,\n"
            "enqueue them, and run DSPy extraction to create memory primitives.\n\n"
            "Time window controls which sessions to scan. You can use:\n"
            "  --window <duration>   a relative window like 7d, 24h, 30m (default: from config)\n"
            "  --window all          scan all sessions ever recorded\n"
            "  --since / --until     absolute ISO-8601 bounds (overrides --window)\n\n"
            "Examples:\n"
            "  lerim sync                          # sync using configured window (default: 7d)\n"
            "  lerim sync --window 30d             # sync last 30 days\n"
            "  lerim sync --window all             # sync everything\n"
            "  lerim sync --agent claude,codex     # only sync these platforms\n"
            "  lerim sync --run-id abc123 --force  # re-extract a specific session\n"
            "  lerim sync --since 2026-02-01T00:00:00Z --until 2026-02-08T00:00:00Z\n"
            "  lerim sync --no-extract             # index and enqueue only, skip extraction\n"
            "  lerim sync --dry-run                # preview what would happen, no writes\n"
            "  lerim sync --max-sessions 100       # process up to 100 sessions"
        ),
    )
    sync.add_argument(
        "--run-id",
        help="Target a single session by its run ID. Bypasses the normal index scan "
        "and fetches this session directly. Use with --force to re-extract.",
    )
    sync.add_argument(
        "--agent",
        help="Comma-separated list of platforms to sync (e.g. 'claude,codex'). "
        "Omit to sync all connected platforms.",
    )
    sync.add_argument(
        "--window",
        default=None,
        help="Time window for session discovery. Accepts durations like 30s, 2m, 1h, 7d, "
        "or the literal 'all' to scan every session. Ignored when --since is set. "
        "(default: sync_window_days from config, currently 7d)",
    )
    sync.add_argument(
        "--since",
        help="Absolute start bound (ISO-8601, e.g. 2026-02-01T00:00:00Z). Overrides --window.",
    )
    sync.add_argument(
        "--until",
        help="Absolute end bound (ISO-8601). Defaults to now if omitted. Only used with --since.",
    )
    sync.add_argument(
        "--max-sessions",
        type=int,
        default=None,
        help="Maximum number of sessions to extract in one run. "
        "(default: sync_max_sessions from config, currently 50)",
    )
    sync.add_argument(
        "--no-extract",
        action="store_true",
        help="Index and enqueue sessions but skip DSPy extraction entirely. "
        "Useful to populate the queue without creating memories yet.",
    )
    sync.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction of sessions that were already processed. "
        "Without this, already-extracted sessions are skipped.",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode: skip all writes (no indexing, no enqueuing, no extraction). "
        "Shows what would happen without changing anything.",
    )
    sync.add_argument(
        "--ignore-lock",
        action="store_true",
        help="Skip the filesystem writer lock. Useful for debugging, but risks "
        "corruption if another sync is running concurrently.",
    )
    sync.set_defaults(func=_cmd_sync)

    # ── maintain ─────────────────────────────────────────────────────
    maintain = sub.add_parser(
        "maintain",
        formatter_class=_F,
        help="Refine existing memories offline (cold path)",
        description=(
            "Cold-path: offline memory refinement. Scans existing memories and\n"
            "merges duplicates, archives low-value items, and consolidates related\n"
            "memories. Archived items go to memory/archived/{decisions,learnings}/.\n\n"
            "Examples:\n"
            "  lerim maintain                # run one maintenance pass\n"
            "  lerim maintain --force        # force maintenance even if recently run\n"
            "  lerim maintain --dry-run      # preview only, no writes"
        ),
    )
    maintain.add_argument(
        "--force",
        action="store_true",
        help="Force maintenance even if a recent run was completed.",
    )
    maintain.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mode: record a run but skip all actual memory changes.",
    )
    maintain.set_defaults(func=_cmd_maintain)

    # ── dashboard ────────────────────────────────────────────────────
    dashboard = sub.add_parser(
        "dashboard",
        formatter_class=_F,
        help="Show local API URL and Lerim Cloud web UI",
        description=(
            "Print the local API base URL and the Lerim Cloud web UI link.\n"
            "The JSON API is served by `lerim serve` (or `lerim up`).\n\n"
            "Examples:\n"
            "  lerim dashboard"
        ),
    )
    dashboard.add_argument(
        "--port",
        type=int,
        help="Port shown in the API URL (default: 8765 or config).",
    )
    dashboard.set_defaults(func=_cmd_dashboard)

    # ── memory (parent group) ────────────────────────────────────────
    memory = sub.add_parser(
        "memory",
        formatter_class=_F,
        help="Memory store operations (search, list, add, reset)",
        description=(
            "Subcommands for managing the memory store directly.\n"
            "Memories are stored as markdown files in .lerim/memory/.\n\n"
            "Subcommands:\n"
            "  search   Search memories by keyword\n"
            "  list     List recent memory items\n"
            "  add      Create a new memory manually\n"
            "  reset    Destructive wipe of memory data"
        ),
    )
    memory_sub = memory.add_subparsers(dest="memory_command")

    memory_search = memory_sub.add_parser(
        "search",
        formatter_class=_F,
        help="Search memories by keyword",
        description=(
            "Full-text keyword search across memory titles, bodies, and tags.\n"
            "Matches are case-insensitive substrings.\n\n"
            "Examples:\n"
            "  lerim memory search 'database migration'\n"
            "  lerim memory search pytest --limit 5"
        ),
    )
    memory_search.add_argument(
        "query", help="Search string to match against memory title, body, and tags."
    )
    memory_search.add_argument(
        "--project", help="Filter results to a specific project (not yet implemented)."
    )
    memory_search.add_argument(
        "--limit", type=int, default=20, help="Maximum results to return. (default: 20)"
    )
    memory_search.set_defaults(func=_cmd_memory_search)

    memory_list = memory_sub.add_parser(
        "list",
        formatter_class=_F,
        help="List recent memory items",
        description=(
            "Display a list of stored memories (decisions and learnings),\n"
            "ordered by recency.\n\n"
            "Examples:\n"
            "  lerim memory list\n"
            "  lerim memory list --limit 10\n"
            "  lerim memory list --json       # structured JSON output"
        ),
    )
    memory_list.add_argument(
        "--project", help="Filter to a specific project (not yet implemented)."
    )
    memory_list.add_argument(
        "--limit", type=int, default=50, help="Maximum items to display. (default: 50)"
    )
    memory_list.set_defaults(func=_cmd_memory_list)

    memory_add = memory_sub.add_parser(
        "add",
        formatter_class=_F,
        help="Manually create a new memory record",
        description=(
            "Create a single memory record from CLI flags.\n"
            "Writes a markdown file to .lerim/memory/{decisions,learnings}/.\n\n"
            "Examples:\n"
            '  lerim memory add --title "Use uv for deps" --body "uv is faster than pip"\n'
            '  lerim memory add --title "API auth pattern" --body "Use bearer tokens" '
            "--primitive decision\n"
            '  lerim memory add --title "Slow test" --body "Integration suite takes 5min" '
            "--kind friction --confidence 0.9 --tags ci,testing"
        ),
    )
    memory_add.add_argument(
        "--title", required=True, help="Short descriptive title for the memory."
    )
    memory_add.add_argument(
        "--body", required=True, help="Full body content of the memory."
    )
    memory_add.add_argument(
        "--primitive",
        default="learning",
        choices=[item.value for item in MemoryType if item != MemoryType.summary],
        help="Primitive type: 'decision' (a choice made) or 'learning' (an insight gained). (default: learning)",
    )
    memory_add.add_argument(
        "--kind",
        default="insight",
        help="Semantic kind label. One of: insight, procedure, friction, pitfall, preference. (default: insight)",
    )
    memory_add.add_argument(
        "--confidence",
        type=float,
        default=0.7,
        help="Confidence score from 0.0 to 1.0. (default: 0.7)",
    )
    memory_add.add_argument(
        "--tags",
        help="Comma-separated tags for categorization (e.g. 'python,testing,ci').",
    )
    memory_add.set_defaults(func=_cmd_memory_add)

    memory_reset = memory_sub.add_parser(
        "reset",
        formatter_class=_F,
        help="DESTRUCTIVE: wipe memory, workspace, and index data",
        description=(
            "Irreversibly delete memory/, workspace/, and index/ under the selected\n"
            "scope, then recreate canonical empty folders.\n\n"
            "Scopes:\n"
            "  project  -- reset <repo>/.lerim/ only\n"
            "  global   -- reset ~/.lerim/ only (includes sessions DB)\n"
            "  both     -- reset both project and global roots (default)\n\n"
            "The sessions DB lives in global index/, so --scope project alone\n"
            "does NOT reset the session queue. Use 'global' or 'both' for a full wipe.\n\n"
            "Examples:\n"
            "  lerim memory reset --yes                     # wipe everything\n"
            "  lerim memory reset --scope project --yes     # project data only\n"
            "  lerim memory reset --yes && lerim sync --max-sessions 5  # fresh start"
        ),
    )
    memory_reset.add_argument(
        "--scope",
        choices=["project", "global", "both"],
        default="both",
        help="What to reset: 'project' (.lerim/ in repo), 'global' (~/.lerim/), or 'both'. (default: both)",
    )
    memory_reset.add_argument(
        "--yes",
        action="store_true",
        help="Required safety flag to confirm destructive reset. Without this, the command refuses to run.",
    )
    memory_reset.set_defaults(func=_cmd_memory_reset)

    # ── ask ──────────────────────────────────────────────────────────
    ask = sub.add_parser(
        "ask",
        formatter_class=_F,
        help="Ask a question using accumulated memory as context",
        description=(
            "One-shot query: ask Lerim a question and get an answer informed by\n"
            "memories extracted from your agent sessions.\n\n"
            "Examples:\n"
            "  lerim ask 'What auth pattern do we use?'\n"
            '  lerim ask "How is the database configured?" --limit 5'
        ),
    )
    ask.add_argument(
        "question", help="Your question (use quotes if it contains spaces)."
    )
    ask.add_argument(
        "--project", help="Scope to a specific project (not yet implemented)."
    )
    ask.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Maximum number of memory items to include as context. (default: 12)",
    )
    ask.set_defaults(func=_cmd_ask)

    # ── status ───────────────────────────────────────────────────────
    status = sub.add_parser(
        "status",
        formatter_class=_F,
        help="Show runtime status (platforms, memory count, queue, last runs)",
        description=(
            "Print a summary of the current Lerim runtime state:\n"
            "connected platforms, memory count, session queue stats,\n"
            "and timestamps of the latest sync/maintain runs.\n\n"
            "Examples:\n"
            "  lerim status\n"
            "  lerim status --json    # structured JSON output"
        ),
    )
    status.set_defaults(func=_cmd_status)

    # ── queue ─────────────────────────────────────────────────────────
    queue = sub.add_parser(
        "queue",
        formatter_class=_F,
        help="Display the session extraction queue",
        description=(
            "Display the session extraction queue with status, project, and age.\n"
            "Host-only command -- reads the local SQLite catalog directly.\n\n"
            "Examples:\n"
            "  lerim queue                      # all active/failed jobs\n"
            "  lerim queue --failed             # dead_letter + failed only\n"
            "  lerim queue --status pending     # filter by status\n"
            "  lerim queue --project lerim-cli  # filter by project\n"
            "  lerim queue --json               # JSON output"
        ),
    )
    queue.add_argument("--failed", action="store_true", help="Show only failed + dead_letter jobs.")
    queue.add_argument("--status", help="Filter by specific status (pending, running, failed, dead_letter, done).")
    queue.add_argument("--project", help="Filter by project name (substring match on repo_path).")
    queue.set_defaults(func=_cmd_queue)

    # ── retry ─────────────────────────────────────────────────────────
    retry = sub.add_parser(
        "retry",
        formatter_class=_F,
        help="Retry dead_letter session jobs",
        description=(
            "Reset dead_letter jobs to pending so the daemon re-processes them.\n"
            "Host-only command -- modifies the local SQLite catalog directly.\n\n"
            "Examples:\n"
            "  lerim retry a1b2c3d4             # retry one job (prefix, min 6 chars)\n"
            "  lerim retry --project lerim-cli  # retry all dead_letter for a project\n"
            "  lerim retry --all                # retry all dead_letter jobs"
        ),
    )
    retry.add_argument("run_id", nargs="?", help="Run ID (or prefix, min 6 chars) of the job to retry.")
    retry.add_argument("--project", help="Retry all dead_letter jobs for a project (by name).")
    retry.add_argument("--all", action="store_true", help="Retry all dead_letter jobs across all projects.")
    retry.set_defaults(func=_cmd_retry)

    # ── skip ──────────────────────────────────────────────────────────
    skip = sub.add_parser(
        "skip",
        formatter_class=_F,
        help="Skip dead_letter session jobs",
        description=(
            "Mark dead_letter jobs as done (skipped), unblocking the project queue.\n"
            "Host-only command -- modifies the local SQLite catalog directly.\n\n"
            "Examples:\n"
            "  lerim skip a1b2c3d4             # skip one job (prefix, min 6 chars)\n"
            "  lerim skip --project lerim-cli  # skip all dead_letter for a project\n"
            "  lerim skip --all                # skip all dead_letter jobs"
        ),
    )
    skip.add_argument("run_id", nargs="?", help="Run ID (or prefix, min 6 chars) of the job to skip.")
    skip.add_argument("--project", help="Skip all dead_letter jobs for a project (by name).")
    skip.add_argument("--all", action="store_true", help="Skip all dead_letter jobs across all projects.")
    skip.set_defaults(func=_cmd_skip)

    # ── init ─────────────────────────────────────────────────────────
    init = sub.add_parser(
        "init",
        formatter_class=_F,
        help="Interactive setup wizard",
        description=(
            "Run the interactive setup wizard. Detects available coding agents,\n"
            "lets you select which ones to use, and writes ~/.lerim/config.toml.\n\n"
            "Examples:\n"
            "  lerim init"
        ),
    )
    init.set_defaults(func=_cmd_init)

    # ── project ──────────────────────────────────────────────────────
    project = sub.add_parser(
        "project",
        formatter_class=_F,
        help="Manage registered projects (add, list, remove)",
        description=(
            "Register, list, or remove projects.\n"
            "Registered projects are mounted into the Docker container.\n\n"
            "Subcommands:\n"
            "  add <path>    Register a project directory\n"
            "  list          Show registered projects\n"
            "  remove <name> Unregister a project"
        ),
    )
    project_sub = project.add_subparsers(dest="project_action")

    proj_add = project_sub.add_parser(
        "add",
        formatter_class=_F,
        help="Register a project directory",
    )
    proj_add.add_argument("path", help="Path to the project directory.")

    project_sub.add_parser(
        "list",
        formatter_class=_F,
        help="List registered projects",
    )

    proj_remove = project_sub.add_parser(
        "remove",
        formatter_class=_F,
        help="Unregister a project",
    )
    proj_remove.add_argument("name", help="Short name of the project to remove.")

    project.set_defaults(func=_cmd_project)

    # ── up ───────────────────────────────────────────────────────────
    up = sub.add_parser(
        "up",
        formatter_class=_F,
        help="Start the Docker container",
        description=(
            "Read ~/.lerim/config.toml, generate docker-compose.yml with volume\n"
            "mounts for agents and projects, and start the container.\n\n"
            "By default the pre-built image is pulled from GHCR. Use --build\n"
            "to build from the local Dockerfile instead.\n\n"
            "Examples:\n"
            "  lerim up              # pull GHCR image\n"
            "  lerim up --build      # build locally from Dockerfile"
        ),
    )
    up.add_argument(
        "--build",
        action="store_true",
        help="Build the Docker image from local Dockerfile instead of pulling from GHCR.",
    )
    up.set_defaults(func=_cmd_up)

    # ── down ─────────────────────────────────────────────────────────
    down = sub.add_parser(
        "down",
        formatter_class=_F,
        help="Stop the Docker container",
        description="Stop the running Lerim Docker container.\n\nExamples:\n  lerim down",
    )
    down.set_defaults(func=_cmd_down)

    # ── logs ─────────────────────────────────────────────────────────
    logs = sub.add_parser(
        "logs",
        formatter_class=_F,
        help="View local Lerim log entries",
        description=(
            "Read and display log entries from ~/.lerim/logs/lerim.jsonl.\n"
            "By default shows the last 50 entries, formatted for the terminal.\n\n"
            "Examples:\n"
            "  lerim logs                   # last 50 entries\n"
            "  lerim logs --level error     # only ERROR entries\n"
            "  lerim logs --since 1h        # entries from the last hour\n"
            "  lerim logs -f                # live tail\n"
            "  lerim logs --json            # raw JSONL output"
        ),
    )
    logs.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Live tail: watch for new log lines and print as they appear.",
    )
    logs.add_argument(
        "--level",
        default=None,
        help="Filter by log level (case-insensitive). E.g. error, warning, info.",
    )
    logs.add_argument(
        "--since",
        default=None,
        help="Show entries from the last N hours/minutes/days. Format: 1h, 30m, 2d.",
    )
    logs.add_argument(
        "--json",
        dest="raw_json",
        action="store_true",
        help="Output raw JSONL lines instead of formatted text.",
    )
    logs.set_defaults(func=_cmd_logs)

    # ── serve ────────────────────────────────────────────────────────
    serve = sub.add_parser(
        "serve",
        formatter_class=_F,
        help="Start HTTP API + daemon loop (Docker entrypoint)",
        description=(
            "Combined server: HTTP JSON API + background daemon loop.\n"
            "Web UI is hosted on Lerim Cloud; GET / may show a stub link.\n"
            "in one process. This is the Docker container entrypoint.\n\n"
            "Examples:\n"
            "  lerim serve\n"
            "  lerim serve --host 0.0.0.0 --port 8765"
        ),
    )
    serve.add_argument("--host", help="Bind address (default: 0.0.0.0).")
    serve.add_argument("--port", type=int, help="Bind port (default: 8765).")
    serve.set_defaults(func=_cmd_serve)

    # ── skill ─────────────────────────────────────────────────────────
    skill = sub.add_parser(
        "skill",
        formatter_class=_F,
        help="Install Lerim skill files into coding agent directories",
        description=(
            "Install bundled skill files (SKILL.md, cli-reference.md) into\n"
            "coding agent skill directories so agents can query Lerim.\n\n"
            "Installs to two locations:\n"
            "  ~/.agents/skills/lerim/  — shared by Cursor, Codex, OpenCode, and others\n"
            "  ~/.claude/skills/lerim/  — Claude Code (reads only from its own dir)\n\n"
            "Examples:\n"
            "  lerim skill install"
        ),
    )
    skill_sub = skill.add_subparsers(dest="skill_action")
    skill_sub.add_parser(
        "install",
        formatter_class=_F,
        help="Copy skill files into agent directories",
    )
    skill.set_defaults(func=_cmd_skill)

    # ── auth ──────────────────────────────────────────────────────────
    auth = sub.add_parser(
        "auth",
        formatter_class=_F,
        help="Authenticate with Lerim Cloud",
        description=(
            "Log in, check status, or log out of Lerim Cloud.\n\n"
            "The default action (no subcommand) opens a browser for OAuth login.\n"
            "Use --token to authenticate manually without a browser.\n\n"
            "Subcommands:\n"
            "  status   Check current authentication state\n"
            "  logout   Remove stored credentials\n\n"
            "Examples:\n"
            "  lerim auth                     # browser-based login\n"
            "  lerim auth --token lerim_tok_abc123  # manual token\n"
            "  lerim auth status              # check auth state\n"
            "  lerim auth logout              # remove token"
        ),
    )
    auth.add_argument(
        "--token",
        default=None,
        help="Authenticate with a token directly (skip browser flow).",
    )
    auth_sub = auth.add_subparsers(dest="auth_command")

    auth_sub.add_parser(
        "login",
        formatter_class=_F,
        help="Log in to Lerim Cloud (same as bare `lerim auth`)",
    )

    auth_sub.add_parser(
        "status",
        formatter_class=_F,
        help="Check current authentication state",
    )

    auth_sub.add_parser(
        "logout",
        formatter_class=_F,
        help="Remove stored credentials",
    )

    auth.set_defaults(func=_cmd_auth_dispatch)

    return parser


_SKIP_TRACING_COMMANDS = frozenset({"auth", "logs", "version", "help", "queue", "retry", "skip"})


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for CLI invocation with global flags and dispatch."""
    raw_argv = list(argv or sys.argv[1:])
    # Determine subcommand early to skip heavy init for lightweight commands.
    first_arg = next((a for a in raw_argv if not a.startswith("-")), None)
    configure_logging()
    if first_arg not in _SKIP_TRACING_COMMANDS:
        configure_tracing(get_config())
    parser = build_parser()
    args = parser.parse_args(_hoist_global_json_flag(list(argv or sys.argv[1:])))

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    if args.command == "memory" and not getattr(args, "memory_command", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "project" and not getattr(args, "project_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    if args.command == "skill" and not getattr(args, "skill_action", None):
        parser.parse_args([args.command, "--help"])
        return 0

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
