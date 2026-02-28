"""Command-line interface for Lerim runtime, memory, and service operations.

Service commands (chat, sync, maintain, status) are thin HTTP clients that
talk to a running Lerim server (started via ``lerim up`` or ``lerim serve``).
Host-only commands (init, project, up, down, logs, connect, memory, daemon)
run locally and never require an HTTP server.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

import frontmatter

from lerim import __version__
from lerim.adapters.registry import (
    KNOWN_PLATFORMS,
    connect_platform,
    list_platforms,
    load_platforms,
    remove_platform,
)
from lerim.app.api import (
    COMPOSE_PATH,
    api_project_add,
    api_project_list,
    api_project_remove,
    api_up,
    api_down,
    detect_agents,
    docker_available,
    is_container_running,
    list_memory_files,
    write_init_config,
)
from lerim.app.arg_utils import parse_csv
from lerim.app.daemon import run_daemon_forever, run_daemon_once
from lerim.config.project_scope import resolve_data_dirs
from lerim.config.logging import configure_logging
from lerim.config.settings import get_config, USER_CONFIG_PATH
from lerim.config.tracing import configure_tracing
from lerim.memory.memory_repo import build_memory_paths, reset_memory_root
from lerim.memory.memory_record import MemoryRecord, MemoryType, memory_folder, slugify


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


def _read_memory_frontmatter(path: Path) -> dict[str, Any] | None:
    """Read frontmatter from a memory markdown file. Returns None on parse error."""
    try:
        post = frontmatter.load(str(path))
        fm = dict(post.metadata)
        fm["_body"] = post.content
        fm["_path"] = str(path)
        return fm
    except Exception:
        return None


def _format_memory_hit(fm: dict[str, Any]) -> str:
    """Render one compact human-readable memory summary line."""
    mid = fm.get("id", "?")
    title = fm.get("title", "?")
    confidence = fm.get("confidence", "?")
    return f"{mid} conf={confidence} title={title}"


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


def _cmd_daemon(args: argparse.Namespace) -> int:
    """Handle daemon commands for one-shot or continuous execution."""
    if args.once:
        payload = run_daemon_once()
        _emit_structured(
            title="Daemon once result:", payload=payload, as_json=args.json
        )
        return 0
    run_daemon_forever(poll_seconds=args.poll_seconds)
    return 0


def _cmd_dashboard(args: argparse.Namespace) -> int:
    """Print the dashboard URL (dashboard is served by lerim serve)."""
    config = get_config()
    port = args.port or config.server_port or 8765
    _emit(f"Dashboard: http://localhost:{port}")
    _emit("The dashboard is served by `lerim serve` (or `lerim up` for Docker).")
    return 0


def search_memory(
    question: str,
    project_filter: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search memory by keyword tokens with file-scan approach."""
    config = get_config()
    files = list_memory_files(config.memory_dir)
    all_fm: list[dict[str, Any]] = []
    for path in files:
        fm = _read_memory_frontmatter(path)
        if fm:
            all_fm.append(fm)
    if not question.strip():
        return all_fm[:limit]
    tokens = [token.lower() for token in question.split() if token.strip()]
    hits: list[dict[str, Any]] = []
    for fm in all_fm:
        haystack = " ".join(
            [
                str(fm.get("title", "")),
                str(fm.get("_body", "")),
                " ".join(fm.get("tags", [])),
            ]
        ).lower()
        if any(token in haystack for token in tokens):
            hits.append(fm)
    return (hits or all_fm)[:limit]


def _cmd_memory_search(args: argparse.Namespace) -> int:
    """Search stored memories and print list or JSON output."""
    hits = search_memory(args.query, limit=args.limit, project_filter=args.project)
    if args.json:
        _emit(json.dumps(hits, indent=2, ensure_ascii=True, default=str))
        return 0
    if not hits:
        _emit("No matching memories.")
        return 0
    for fm in hits:
        _emit(_format_memory_hit(fm))
    return 0


def _cmd_memory_list(args: argparse.Namespace) -> int:
    """List recent memories, optionally filtered by project."""
    config = get_config()
    files = list_memory_files(config.memory_dir)
    items: list[dict[str, Any]] = []
    for path in files:
        fm = _read_memory_frontmatter(path)
        if fm:
            items.append(fm)
    items = items[: args.limit]
    if args.json:
        _emit(json.dumps(items, indent=2, ensure_ascii=True, default=str))
        return 0
    for fm in items:
        _emit(_format_memory_hit(fm))
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
    )
    folder = config.memory_dir / memory_folder(primitive)
    folder.mkdir(parents=True, exist_ok=True)
    filename = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%d')}-{slugify(args.title)}.md"
    )
    filepath = folder / filename
    filepath.write_text(record.to_markdown(), encoding="utf-8")
    _emit(f"Added memory: {record.id} -> {filepath}")
    return 0


def _cmd_memory_export(args: argparse.Namespace) -> int:
    """Export memories as Markdown or JSON to stdout or a file."""
    config = get_config()
    files = list_memory_files(config.memory_dir)
    items: list[dict[str, Any]] = []
    for path in files:
        fm = _read_memory_frontmatter(path)
        if fm:
            items.append(fm)

    if args.format == "json":
        output = json.dumps(items, indent=2, ensure_ascii=True, default=str)
    else:
        lines = ["# Lerim Memory Export", ""]
        for fm in items:
            title = fm.get("title", "?")
            mid = fm.get("id", "?")
            confidence = fm.get("confidence", "?")
            body = str(fm.get("_body", "")).strip()[:260]
            lines.append(f"## {title} ({mid})")
            lines.append(f"- confidence: {confidence}")
            lines.append(body)
            lines.append("")
        output = "\n".join(lines)
    if args.output:
        path = Path(args.output).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            output + ("\n" if not output.endswith("\n") else ""), encoding="utf-8"
        )
        _emit(f"Exported: {path}")
    else:
        _emit(output)
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    """Forward chat query to the running Lerim server."""
    data = _api_post("/api/chat", {"question": args.question, "limit": args.limit})
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
        _emit(f"- queue: {data.get('queue', {})}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    """Interactive setup wizard that writes ~/.lerim/config.toml."""
    _emit("Welcome to Lerim.\n")
    _emit("Which coding agents do you use?")

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
        _emit(f"\nConfig written to {USER_CONFIG_PATH}")
        _emit(f"Agents: {', '.join(selected.keys())}")
    else:
        _emit("\nNo agents selected. You can add them later by editing:")
        _emit(f"  {USER_CONFIG_PATH}")

    # Check Docker
    if docker_available():
        _emit("\nDocker: found")
    else:
        _emit("\nDocker: not found")
        _emit("  Install Docker to use `lerim up` (recommended).")
        _emit("  Or run `lerim serve` directly without Docker.")

    _emit("\nNext steps:")
    _emit("  lerim project add /path/to/repo   # register a project")
    _emit("  lerim up                           # start the Docker service")
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
    result = api_up()
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return 1
    _emit(f"Lerim is running at http://localhost:{config.server_port}")
    return 0


def _cmd_down(args: argparse.Namespace) -> int:
    """Stop the Docker container."""
    result = api_down()
    if result.get("error"):
        _emit(result["error"], file=sys.stderr)
        return 1
    _emit("Lerim stopped.")
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    """Tail Docker container logs."""
    if not COMPOSE_PATH.exists():
        _emit("No compose file found. Run `lerim up` first.", file=sys.stderr)
        return 1
    cmd = ["docker", "compose", "-f", str(COMPOSE_PATH), "logs"]
    if args.follow:
        cmd.append("--follow")
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Start HTTP API + dashboard + daemon loop in one process."""
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

    stop_event = threading.Event()

    def _daemon_loop() -> None:
        """Background daemon loop running sync + maintain cycles."""
        from lerim.config.logging import logger

        interval = max(config.poll_interval_minutes * 60, 30)
        while not stop_event.is_set():
            try:
                run_daemon_once()
            except Exception as exc:
                logger.warning("daemon cycle error: {}", exc)
            stop_event.wait(interval)

    daemon_thread = threading.Thread(
        target=_daemon_loop, name="lerim-daemon", daemon=True
    )
    daemon_thread.start()

    def _shutdown(signum: int, frame: Any) -> None:
        """Handle graceful shutdown on SIGTERM/SIGINT."""
        stop_event.set()
        httpd.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    from lerim.config.logging import logger

    logger.info("Lerim serve running at http://{}:{}/", host, port)
    httpd.serve_forever()
    stop_event.set()
    daemon_thread.join(timeout=5)
    return 0


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

    # ── daemon ───────────────────────────────────────────────────────
    daemon = sub.add_parser(
        "daemon",
        formatter_class=_F,
        help="Run recurring sync + maintain loop",
        description=(
            "Runs a continuous loop: sync (index + extract) then maintain (refine),\n"
            "repeating at a configurable interval. Use --once for a single cycle.\n\n"
            "Examples:\n"
            "  lerim daemon                  # run forever with default poll interval\n"
            "  lerim daemon --once           # run one sync+maintain cycle and exit\n"
            "  lerim daemon --poll-seconds 120  # poll every 2 minutes"
        ),
    )
    daemon.add_argument(
        "--once",
        action="store_true",
        help="Run exactly one sync+maintain cycle and exit (instead of looping forever).",
    )
    daemon.add_argument(
        "--poll-seconds",
        type=int,
        help="Seconds between cycles. Overrides config poll_interval_minutes. Minimum 30s.",
    )
    daemon.set_defaults(func=_cmd_daemon)

    # ── dashboard ────────────────────────────────────────────────────
    dashboard = sub.add_parser(
        "dashboard",
        formatter_class=_F,
        help="Show dashboard URL",
        description=(
            "Print the dashboard URL. The dashboard is served by `lerim serve`\n"
            "(or `lerim up` for Docker). No separate server is started.\n\n"
            "Examples:\n"
            "  lerim dashboard"
        ),
    )
    dashboard.add_argument(
        "--port",
        type=int,
        help="Bind port for the dashboard server (default: 8765 or config value).",
    )
    dashboard.set_defaults(func=_cmd_dashboard)

    # ── memory (parent group) ────────────────────────────────────────
    memory = sub.add_parser(
        "memory",
        formatter_class=_F,
        help="Memory store operations (search, list, add, export, reset)",
        description=(
            "Subcommands for managing the memory store directly.\n"
            "Memories are stored as markdown files in .lerim/memory/.\n\n"
            "Subcommands:\n"
            "  search   Search memories by keyword\n"
            "  list     List recent memory items\n"
            "  add      Create a new memory manually\n"
            "  export   Export all memories to file or stdout\n"
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

    memory_export = memory_sub.add_parser(
        "export",
        formatter_class=_F,
        help="Export all memories to file or stdout",
        description=(
            "Export every memory record as JSON or markdown.\n"
            "Prints to stdout by default, or writes to a file with --output.\n\n"
            "Examples:\n"
            "  lerim memory export                          # markdown to stdout\n"
            "  lerim memory export --format json            # JSON to stdout\n"
            "  lerim memory export --format json --output memories.json"
        ),
    )
    memory_export.add_argument(
        "--project", help="Filter to a specific project (not yet implemented)."
    )
    memory_export.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format: 'json' (frontmatter dicts) or 'markdown' (heading + body preview). (default: markdown)",
    )
    memory_export.add_argument(
        "--output",
        help="File path to write to. Creates parent directories if needed. Omit to print to stdout.",
    )
    memory_export.set_defaults(func=_cmd_memory_export)

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

    # ── chat ─────────────────────────────────────────────────────────
    chat = sub.add_parser(
        "chat",
        formatter_class=_F,
        help="Ask a question using accumulated memory as context",
        description=(
            "One-shot query: ask Lerim a question and get an answer informed by\n"
            "memories extracted from your agent sessions.\n\n"
            "Examples:\n"
            "  lerim chat 'What auth pattern do we use?'\n"
            '  lerim chat "How is the database configured?" --limit 5'
        ),
    )
    chat.add_argument(
        "question", help="Your question (use quotes if it contains spaces)."
    )
    chat.add_argument(
        "--project", help="Scope to a specific project (not yet implemented)."
    )
    chat.add_argument(
        "--limit",
        type=int,
        default=12,
        help="Maximum number of memory items to include as context. (default: 12)",
    )
    chat.set_defaults(func=_cmd_chat)

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
            "Examples:\n"
            "  lerim up"
        ),
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
        help="Tail Docker container logs",
        description=(
            "View the Lerim Docker container logs.\n\n"
            "Examples:\n"
            "  lerim logs\n"
            "  lerim logs --follow"
        ),
    )
    logs.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="Follow log output (like tail -f).",
    )
    logs.set_defaults(func=_cmd_logs)

    # ── serve ────────────────────────────────────────────────────────
    serve = sub.add_parser(
        "serve",
        formatter_class=_F,
        help="Start HTTP API + dashboard + daemon loop (Docker entrypoint)",
        description=(
            "Combined server: HTTP API + dashboard + background daemon loop\n"
            "in one process. This is the Docker container entrypoint.\n\n"
            "Examples:\n"
            "  lerim serve\n"
            "  lerim serve --host 0.0.0.0 --port 8765"
        ),
    )
    serve.add_argument("--host", help="Bind address (default: 0.0.0.0).")
    serve.add_argument("--port", type=int, help="Bind port (default: 8765).")
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for CLI invocation with global flags and dispatch."""
    configure_logging()
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

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
