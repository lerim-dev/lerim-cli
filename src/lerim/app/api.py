"""Shared API logic for CLI and HTTP endpoints.

Extracts the core business logic for chat, sync, maintain, and project
management so both the argparse CLI and the HTTP API call the same code.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lerim import __version__
from lerim.adapters.registry import (
    connect_platform,
    list_platforms,
)
from lerim.app.arg_utils import parse_agent_filter, parse_duration_to_seconds
from lerim.app.daemon import (
    resolve_window_bounds,
    run_maintain_once,
    run_sync_once,
)
from lerim.config.settings import (
    get_config,
    reload_config,
    save_config_patch,
    USER_CONFIG_PATH,
)
from lerim.memory.memory_record import MemoryType, memory_folder
from lerim.runtime.agent import LerimAgent
from lerim.runtime.prompts.chat import build_chat_prompt, looks_like_auth_error
from lerim.sessions.catalog import (
    count_fts_indexed,
    count_session_jobs_by_status,
    latest_service_run,
)


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


def api_chat(question: str, limit: int = 12) -> dict[str, Any]:
    """Run one chat query against the runtime agent and return result dict."""
    config = get_config()
    memory_root = str(config.memory_dir)
    hits: list[dict[str, Any]] = []
    context_docs: list[dict[str, Any]] = []
    prompt = build_chat_prompt(question, hits, context_docs, memory_root=memory_root)
    agent = LerimAgent()
    response, session_id = agent.chat(
        prompt, cwd=str(Path.cwd()), memory_root=memory_root
    )
    error = looks_like_auth_error(response)
    return {
        "answer": response,
        "agent_session_id": session_id,
        "memories_used": [fm.get("id", "") for fm in hits],
        "error": bool(error),
    }


def api_sync(
    agent: str | None = None,
    window: str | None = None,
    max_sessions: int | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run one sync cycle and return summary dict."""
    config = get_config()
    window_start, window_end = resolve_window_bounds(
        window=window or f"{config.sync_window_days}d",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )
    code, summary = run_sync_once(
        run_id=None,
        agent_filter=parse_agent_filter(agent) if agent else None,
        no_extract=False,
        force=force,
        max_sessions=max_sessions or config.sync_max_sessions,
        dry_run=dry_run,
        ignore_lock=False,
        trigger="api",
        window_start=window_start,
        window_end=window_end,
    )
    return {"code": code, **asdict(summary)}


def api_maintain(force: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Run one maintain cycle and return result dict."""
    code, payload = run_maintain_once(force=force, dry_run=dry_run)
    return {"code": code, **payload}


def list_memory_files(memory_dir: Path) -> list[Path]:
    """List all markdown files in canonical memory primitive folders."""
    paths: list[Path] = []
    for mtype in MemoryType:
        folder = memory_dir / memory_folder(mtype)
        if folder.exists():
            paths.extend(sorted(folder.rglob("*.md")))
    return paths


def api_status() -> dict[str, Any]:
    """Return runtime status summary."""
    config = get_config()
    memory_count = len(list_memory_files(config.memory_dir))
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "connected_agents": list(config.agents.keys()),
        "platforms": list_platforms(config.platforms_path),
        "memory_count": memory_count,
        "sessions_indexed_count": count_fts_indexed(),
        "queue": count_session_jobs_by_status(),
        "latest_sync": latest_service_run("sync"),
        "latest_maintain": latest_service_run("maintain"),
    }


def api_connect_list() -> list[dict[str, Any]]:
    """Return list of connected platforms."""
    config = get_config()
    return list_platforms(config.platforms_path)


def api_connect(platform: str, path: str | None = None) -> dict[str, Any]:
    """Connect a platform and return result."""
    config = get_config()
    return connect_platform(config.platforms_path, platform, custom_path=path)


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
    # Create .lerim/ in project
    lerim_dir = resolved / ".lerim"
    lerim_dir.mkdir(parents=True, exist_ok=True)

    # Update config
    save_config_patch({"projects": {name: str(resolved)}})

    return {"name": name, "path": str(resolved), "created_lerim_dir": True}


def api_project_remove(name: str) -> dict[str, Any]:
    """Unregister a project by name."""
    config = get_config()
    if name not in config.projects:
        return {"error": f"Project not registered: {name}", "removed": False}

    # Read current user config, remove the project, write back
    import tomllib

    user_path = USER_CONFIG_PATH
    existing: dict[str, Any] = {}
    if user_path.exists():
        with open(user_path, "rb") as fh:
            existing = tomllib.load(fh)

    projects = existing.get("projects", {})
    if isinstance(projects, dict) and name in projects:
        del projects[name]
        existing["projects"] = projects
        save_config_patch(existing)

    reload_config()
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
    """Check if Docker is installed and accessible."""
    try:
        subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def write_init_config(selected_agents: dict[str, str]) -> Path:
    """Write initial [agents] config and return the config file path."""
    save_config_patch({"agents": selected_agents})
    return USER_CONFIG_PATH


# ── Docker management ────────────────────────────────────────────────


COMPOSE_PATH = Path.home() / ".lerim" / "docker-compose.yml"


def _generate_compose_yml() -> str:
    """Generate docker-compose.yml content from current config."""
    config = reload_config()
    home = str(Path.home())

    volumes = [f"      - {home}/.lerim:{home}/.lerim"]

    # Agent session dirs (read-only)
    for _name, path_str in config.agents.items():
        resolved = str(Path(path_str).expanduser().resolve())
        volumes.append(f"      - {resolved}:{resolved}:ro")

    # Project dirs
    for _name, path_str in config.projects.items():
        resolved = str(Path(path_str).expanduser().resolve())
        volumes.append(f"      - {resolved}:{resolved}")

    volumes_block = "\n".join(volumes)
    port = config.server_port

    return f"""\
# Auto-generated by lerim up — do not edit manually.
# Regenerated from ~/.lerim/config.toml on every `lerim up`.
services:
  lerim:
    image: lerim
    container_name: lerim
    restart: unless-stopped
    ports:
      - "127.0.0.1:{port}:{port}"
    environment:
      - HOME={home}
    volumes:
{volumes_block}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:{port}/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
"""


def api_up() -> dict[str, Any]:
    """Generate compose file and start Docker container."""
    if not docker_available():
        return {"error": "Docker is not installed or not running."}

    compose_content = _generate_compose_yml()
    COMPOSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMPOSE_PATH.write_text(compose_content, encoding="utf-8")

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_PATH), "up", "-d"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "docker compose up failed"}

    return {"status": "started", "compose_path": str(COMPOSE_PATH)}


def api_down() -> dict[str, Any]:
    """Stop Docker container."""
    if not COMPOSE_PATH.exists():
        return {"error": "No compose file found. Run `lerim up` first."}

    result = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE_PATH), "down"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": result.stderr.strip() or "docker compose down failed"}
    return {"status": "stopped"}


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

    mem_files = list_memory_files(Path("/tmp/nonexistent"))
    assert mem_files == [], f"expected empty list, got {mem_files}"

    print(
        f"api.py self-test passed: health={health}, docker={docker_ok}, mem_files={mem_files}"
    )
