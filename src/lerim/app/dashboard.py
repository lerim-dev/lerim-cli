"""Read-only dashboard HTTP server and memory graph/query API handlers.

Serves a single-page Alpine.js dashboard with ECharts visualizations.
Backend provides JSON APIs for session stats, run listings, memory
exploration, pipeline status, and config management.
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from lerim.adapters.common import load_jsonl_dict_lines
from lerim.config.logging import logger
from lerim.app.api import (
    api_chat,
    api_connect,
    api_connect_list,
    api_health,
    api_maintain,
    api_project_add,
    api_project_list,
    api_project_remove,
    api_status,
    api_sync,
)
from lerim.config.settings import (
    Config,
    get_config,
    get_config_sources,
    get_user_config_path,
    save_config_patch,
)
from lerim.memory.extract_pipeline import build_extract_report
import frontmatter as fm_lib

from lerim.memory.memory_record import MemoryType, memory_folder
from lerim.runtime.providers import list_provider_models
from lerim.sessions.catalog import (
    count_session_jobs_by_status,
    fetch_session_doc,
    init_sessions_db,
    latest_service_run,
    list_sessions_window,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = REPO_ROOT / "dashboard"
MAX_BODY_BYTES = 1_000_000
READ_ONLY_MESSAGE = "Dashboard is read-only. Use CLI commands for write actions."
_REPORT_CACHE: dict[str, Any] = {"at": None, "value": None}
_SESSION_DETAILS_CACHE: dict[str, dict[str, Any]] = {}


def _iso_now() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _extract_session_details(session_path: str) -> dict[str, Any]:
    """Extract model name and tool usage counts from a session JSONL trace.

    Results are cached per session_path for the server lifetime.
    Handles Claude, Codex, and OpenCode JSONL formats.
    """
    if session_path in _SESSION_DETAILS_CACHE:
        return _SESSION_DETAILS_CACHE[session_path]
    result: dict[str, Any] = {"model": "", "tools": {}}

    def _pick_model(row: dict[str, Any], msg_obj: Any, payload_obj: Any) -> str:
        """Pick best-effort model id from known trace formats."""
        msg = msg_obj if isinstance(msg_obj, dict) else {}
        payload = payload_obj if isinstance(payload_obj, dict) else {}
        model_cfg = row.get("modelConfig")
        model_info = row.get("modelInfo")
        payload_info = payload.get("info")
        collab = payload.get("collaboration_mode")
        collab_settings = collab.get("settings") if isinstance(collab, dict) else None
        payload_session = payload.get("session")
        candidates: list[Any] = [
            row.get("model"),
            row.get("model_name"),
            msg.get("model"),
            model_cfg.get("modelName") if isinstance(model_cfg, dict) else "",
            model_info.get("modelName") if isinstance(model_info, dict) else "",
            payload.get("model"),
            payload_info.get("model") if isinstance(payload_info, dict) else "",
            collab_settings.get("model") if isinstance(collab_settings, dict) else "",
            payload_session.get("model") if isinstance(payload_session, dict) else "",
        ]
        for value in candidates:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    try:
        rows = load_jsonl_dict_lines(Path(session_path).expanduser())
        for row in rows:
            # Claude format: message.model, tool_use blocks in content
            msg = row.get("message") if isinstance(row.get("message"), dict) else {}
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

            picked_model = _pick_model(row, msg, payload)
            if picked_model and not result["model"]:
                result["model"] = picked_model

            for block in (
                msg.get("content", []) if isinstance(msg.get("content"), list) else []
            ):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", "unknown"))
                    result["tools"][name] = result["tools"].get(name, 0) + 1

            # OpenCode format: model field at top level, tool_name field
            if row.get("tool_name"):
                name = str(row["tool_name"])
                result["tools"][name] = result["tools"].get(name, 0) + 1

            # Cursor format tool call events
            if row.get("type") == "tool_call" and row.get("name"):
                name = str(row["name"])
                result["tools"][name] = result["tools"].get(name, 0) + 1

            # Codex format: type=response_item with function_call
            if payload.get("type") == "function_call":
                name = str(payload.get("name", "unknown"))
                result["tools"][name] = result["tools"].get(name, 0) + 1
    except Exception:
        pass
    _SESSION_DETAILS_CACHE[session_path] = result
    return result


def _parse_int(
    raw: str | None, default: int, *, minimum: int = 0, maximum: int = 10_000
) -> int:
    """Parse integer query/body parameter and clamp to bounds."""
    try:
        value = int(raw or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _scope_bounds(scope: str | None) -> tuple[datetime | None, datetime]:
    """Resolve dashboard scope token to time window bounds."""
    now = datetime.now(timezone.utc)
    normalized = (scope or "week").strip().lower()
    if normalized == "today":
        return now - timedelta(days=1), now
    if normalized == "week":
        return now - timedelta(days=7), now
    if normalized == "month":
        return now - timedelta(days=30), now
    if normalized == "all":
        return None, now
    return now - timedelta(days=7), now


def _sqlite_rows(
    since: datetime | None,
    until: datetime,
    agent_type: str | None,
) -> list[sqlite3.Row]:
    """Load session rows for dashboard statistics queries."""
    config = get_config()
    init_sessions_db()
    where: list[str] = []
    params: list[Any] = []
    if since is not None:
        where.append("(start_time >= ? OR start_time IS NULL)")
        params.append(since.isoformat())
    where.append("(start_time <= ? OR start_time IS NULL)")
    params.append(until.isoformat())
    if agent_type and agent_type != "all":
        where.append("agent_type = ?")
        params.append(agent_type)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = f"""\
SELECT run_id, agent_type, repo_name, start_time, status, duration_ms, message_count, \
tool_call_count, error_count, total_tokens, summary_text, session_path \
FROM session_docs {where_sql} ORDER BY start_time DESC, indexed_at DESC"""
    with sqlite3.connect(config.sessions_db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql, params).fetchall()


def _serialize_run(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a DB row to dashboard run JSON payload shape."""
    started = row.get("start_time")
    repo_name = row.get("repo_name") or ""
    session_path = str(row.get("session_path") or "")
    # Extract project name from repo_path (folder path) or session_path
    repo_path = str(row.get("repo_path") or "")
    project = ""
    if repo_path:
        project = Path(repo_path).name
    elif session_path:
        # Claude paths: ~/.claude/projects/-Users-...-project/uuid.jsonl
        sp = Path(session_path)
        if "projects" in sp.parts:
            idx = sp.parts.index("projects")
            if idx + 1 < len(sp.parts):
                encoded = sp.parts[idx + 1]
                project = encoded.rsplit("-", 1)[-1] if "-" in encoded else encoded
    # Build display label: prefer branch/project over raw path
    branch_display = repo_name
    if (
        branch_display
        and "/" in branch_display
        and not branch_display.startswith(("feat/", "fix/", "main", "master", "dev"))
    ):
        # Looks like a full path, extract last component
        branch_display = Path(branch_display).name
    run_id = row.get("run_id") or ""
    short_id = str(run_id)[:8] if run_id else ""
    return {
        "run_id": run_id,
        "agent_type": row.get("agent_type") or "unknown",
        "status": row.get("status") or "completed",
        "started_at": started,
        "duration_ms": int(row.get("duration_ms") or 0),
        "message_count": int(row.get("message_count") or 0),
        "tool_call_count": int(row.get("tool_call_count") or 0),
        "error_count": int(row.get("error_count") or 0),
        "total_tokens": int(row.get("total_tokens") or 0),
        "repo_name": repo_name,
        "project": project,
        "branch_display": branch_display or project or short_id,
        "short_id": short_id,
        "session_path": session_path,
        "snippet": row.get("summary_text") or "",
        "preview": row.get("summary_text") or "",
        "source": "trace",
    }


def _compute_stats(rows: list[sqlite3.Row]) -> dict[str, Any]:
    """Aggregate dashboard metrics across session rows."""
    totals = {
        "runs": len(rows),
        "messages": 0,
        "tool_calls": 0,
        "errors": 0,
        "tokens": 0,
        "duration_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "runs_with_errors": 0,
        "unique_tools": 0,
    }
    by_agent: dict[str, dict[str, int]] = {}
    daily: dict[str, dict[str, int]] = {}
    hourly: dict[int, dict[str, int]] = {}
    for row in rows:
        agent = str(row["agent_type"] or "unknown")
        start_time = str(row["start_time"] or "")
        messages = int(row["message_count"] or 0)
        tools = int(row["tool_call_count"] or 0)
        errors = int(row["error_count"] or 0)
        tokens = int(row["total_tokens"] or 0)
        duration = int(row["duration_ms"] or 0)

        totals["messages"] += messages
        totals["tool_calls"] += tools
        totals["errors"] += errors
        totals["tokens"] += tokens
        totals["duration_ms"] += duration
        if errors > 0 or str(row["status"] or "").strip().lower() == "error":
            totals["runs_with_errors"] += 1

        agent_stats = by_agent.setdefault(
            agent, {"runs": 0, "messages": 0, "tool_calls": 0, "tokens": 0}
        )
        agent_stats["runs"] += 1
        agent_stats["messages"] += messages
        agent_stats["tool_calls"] += tools
        agent_stats["tokens"] += tokens

        try:
            dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        day_key = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        hour_key = int(dt.astimezone(timezone.utc).hour)
        bucket = daily.setdefault(
            day_key, {"messages": 0, "tool_calls": 0, "tokens": 0}
        )
        bucket[agent] = bucket.get(agent, 0) + 1
        bucket["messages"] += messages
        bucket["tool_calls"] += tools
        bucket["tokens"] += tokens
        hour_bucket = hourly.setdefault(
            hour_key, {"sessions": 0, "messages": 0, "tool_calls": 0, "tokens": 0}
        )
        hour_bucket["sessions"] += 1
        hour_bucket["messages"] += messages
        hour_bucket["tool_calls"] += tools
        hour_bucket["tokens"] += tokens

    # Extract model and tool usage from JSONL traces (cached per session)
    model_usage: dict[str, dict[str, int]] = {}
    tool_usage: dict[str, int] = {}
    max_detail_sessions = 200
    for row in rows[:max_detail_sessions]:
        try:
            session_path = str(row["session_path"] or "").strip()
        except (KeyError, IndexError):
            continue
        if not session_path:
            continue
        details = _extract_session_details(session_path)
        model_name = str(details.get("model") or "").strip()
        if model_name:
            bucket = model_usage.setdefault(
                model_name, {"input": 0, "output": 0, "total": 0}
            )
            tokens = int(row["total_tokens"] or 0)
            bucket["total"] += tokens
            bucket["input"] += tokens // 2  # approximate split
            bucket["output"] += tokens - (tokens // 2)
        for tool_name, count in details.get("tools", {}).items():
            tool_usage[tool_name] = tool_usage.get(tool_name, 0) + count

    totals["unique_tools"] = len(tool_usage)
    if totals["tokens"] > 0:
        totals["input_tokens"] = totals["tokens"] // 2
        totals["output_tokens"] = totals["tokens"] - totals["input_tokens"]

    runs = totals["runs"] or 1
    duration_data_available = totals["duration_ms"] > 0
    run_error_rate = round((totals["runs_with_errors"] / runs) * 100, 2)
    avg_messages = round(totals["messages"] / runs, 2)
    avg_tool_calls = round(totals["tool_calls"] / runs, 2)
    avg_duration = round(totals["duration_ms"] / runs, 2)
    derived = {
        "avg_messages_per_session": avg_messages,
        "avg_tool_calls_per_session": avg_tool_calls,
        "avg_session_duration_ms": avg_duration,
        "error_rate": run_error_rate,
        "duration_data_available": duration_data_available,
        # Backward-compatible aliases.
        "avg_messages_per_run": avg_messages,
        "avg_tool_calls_per_run": avg_tool_calls,
        "avg_duration_ms": avg_duration,
        "run_error_rate": run_error_rate,
    }
    daily_activity = []
    for day in sorted(daily.keys()):
        item = {"date": day, **daily[day]}
        daily_activity.append(item)
    hourly_activity = []
    for hour in sorted(hourly.keys()):
        hourly_activity.append({"hour": hour, **hourly[hour]})
    return {
        "totals": totals,
        "derived": derived,
        "by_agent": by_agent,
        "model_usage": model_usage,
        "tool_usage": tool_usage,
        "daily_activity": daily_activity,
        "hourly_activity": hourly_activity,
        "cache": {
            "cached_at": _iso_now(),
            "age_seconds": 0,
            "stale": False,
            "source": "live",
        },
    }


def _load_messages_for_run(run_doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Load normalized message list from source trace path in a run document."""
    session_path = str(run_doc.get("session_path") or "").strip()
    if not session_path:
        return []
    rows = load_jsonl_dict_lines(Path(session_path).expanduser())
    output: list[dict[str, Any]] = []
    for row in rows:
        content = row.get("content")
        if content is None and isinstance(row.get("message"), dict):
            content = row.get("message", {}).get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=True)
        output.append(
            {
                "role": row.get("role") or "assistant",
                "content": str(content or ""),
                "timestamp": row.get("timestamp"),
                "tool_name": row.get("tool_name"),
                "tool_input": row.get("tool_input"),
                "tool_output": row.get("tool_output"),
            }
        )
    return output


def _list_memory_files_dashboard() -> list[Path]:
    """List all markdown files in canonical memory primitive folders."""
    config = get_config()
    paths: list[Path] = []
    for mtype in MemoryType:
        folder = config.memory_dir / memory_folder(mtype)
        if folder.exists():
            paths.extend(sorted(folder.rglob("*.md")))
    return paths


def _read_fm(path: Path) -> dict[str, Any] | None:
    """Read frontmatter from a memory file, returning None on error."""
    try:
        post = fm_lib.load(str(path))
        fm = dict(post.metadata)
        fm["_body"] = post.content
        fm["_path"] = str(path)
        return fm
    except Exception:
        return None


def _load_all_memories() -> list[dict[str, Any]]:
    """Load all memory files as frontmatter dicts."""
    items: list[dict[str, Any]] = []
    for path in _list_memory_files_dashboard():
        fm = _read_fm(path)
        if fm:
            items.append(fm)
    return items


def _serialize_memory(fm: dict[str, Any], *, with_body: bool) -> dict[str, Any]:
    """Serialize memory frontmatter dict for dashboard APIs."""
    payload = {k: v for k, v in fm.items() if not k.startswith("_")}
    if with_body:
        payload["body"] = fm.get("_body", "")
    else:
        body = str(fm.get("_body", "")).strip()[:240]
        payload["snippet"] = body
        payload["preview"] = body
    return payload


def _filter_memories(
    all_items: list[dict[str, Any]],
    *,
    query: str | None,
    type_filter: str | None,
    state_filter: str | None,
    project_filter: str | None,
) -> list[dict[str, Any]]:
    """Apply lightweight memory filters for dashboard list/query APIs."""
    items = all_items
    if type_filter:
        items = [item for item in items if _detect_primitive(item) == type_filter]
    if state_filter:
        items = [
            item
            for item in items
            if str(item.get("kind", "")).lower() == state_filter.lower()
        ]
    if project_filter:
        token = project_filter.strip().lower()
        if token:
            items = [
                item
                for item in items
                if token in str(item.get("source", "")).lower()
                or token in str(item.get("_path", "")).lower()
                or any(token in str(t).lower() for t in item.get("tags", []))
            ]
    if query:
        token = query.strip().lower()
        if token:
            items = [
                item
                for item in items
                if token in str(item.get("title", "")).lower()
                or token in str(item.get("_body", "")).lower()
                or any(token in str(t).lower() for t in item.get("tags", []))
            ]
    return items


def _detect_primitive(fm: dict[str, Any]) -> str:
    """Detect primitive type from frontmatter path or fields."""
    path = str(fm.get("_path", ""))
    if "/decisions/" in path:
        return "decision"
    if "/summaries/" in path:
        return "summary"
    return "learning"


def _edge_id(source: str, target: str, kind: str) -> str:
    """Build stable edge identity for in-memory graph payload dedupe."""
    return f"{source}|{target}|{kind}"


def _memory_graph_options(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return available filter options derived from loaded memory items."""
    types = sorted({_detect_primitive(fm) for fm in items})
    kinds = sorted({str(fm.get("kind", "")) for fm in items if fm.get("kind")})
    tags = sorted({str(t) for fm in items for t in fm.get("tags", []) if t})
    projects: set[str] = set()
    for fm in items:
        # Try to extract project from file path
        path = str(fm.get("_path", ""))
        if ".lerim" in path:
            parent = path.split(".lerim")[0].rstrip("/")
            if parent:
                projects.add(Path(parent).name)
    return {
        "types": types,
        "kinds": kinds,
        "states": [],
        "projects": sorted(projects),
        "tags": tags,
    }


def _graph_filter_values(filters: dict[str, Any], key: str) -> list[str]:
    """Normalize scalar/list filter values from graph query payload."""
    raw = filters.get(key)
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _graph_limits(
    payload: dict[str, Any],
    *,
    default_nodes: int,
    default_edges: int,
    minimum_edges: int,
) -> tuple[int, int]:
    """Resolve node/edge limits with safe bounds."""
    limits = payload.get("limits") or {}
    max_nodes = _parse_int(
        str(limits.get("max_nodes") or str(default_nodes)),
        default_nodes,
        minimum=50,
        maximum=5000,
    )
    max_edges = _parse_int(
        str(limits.get("max_edges") or str(default_edges)),
        default_edges,
        minimum=minimum_edges,
        maximum=12000,
    )
    return max_nodes, max_edges


def _load_memory_graph_edges(
    *,
    memory_ids: list[str] | None = None,
    seed_memory_id: str | None = None,
    limit: int = 4000,
) -> list[tuple[str, str, str, float]]:
    """Load explicit memory graph edges from optional graph SQLite index."""
    config = get_config()
    graph_path = config.graph_db_path or (config.index_dir / "graph.sqlite3")
    if not graph_path.exists():
        return []
    try:
        with sqlite3.connect(graph_path) as conn:
            conn.row_factory = sqlite3.Row
            if seed_memory_id:
                rows = conn.execute(
                    """
                    SELECT source_id, target_id, reason, score
                    FROM graph_edges
                    WHERE source_id = ? OR target_id = ?
                    LIMIT ?
                    """,
                    (seed_memory_id, seed_memory_id, max(1, int(limit))),
                ).fetchall()
            elif memory_ids:
                placeholders = ",".join("?" for _ in memory_ids)
                rows = conn.execute(
                    f"""
                    SELECT source_id, target_id, reason, score
                    FROM graph_edges
                    WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
                    LIMIT ?
                    """,
                    [*memory_ids, *memory_ids, max(1, int(limit))],
                ).fetchall()
            else:
                rows = []
    except sqlite3.Error:
        return []
    return [
        (
            str(row["source_id"] or ""),
            str(row["target_id"] or ""),
            str(row["reason"] or "related"),
            float(row["score"] or 0.5),
        )
        for row in rows
    ]


def _build_memory_graph_payload(
    *,
    selected: list[dict[str, Any]],
    matched_memories: int,
    max_nodes: int,
    max_edges: int,
) -> dict[str, Any]:
    """Build graph explorer nodes/edges payload from selected memory dicts."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def add_node(
        node_id: str, *, label: str, kind: str, score: float, properties: dict[str, Any]
    ) -> None:
        """Insert one graph node once, preserving first-seen payload."""
        if node_id in nodes:
            return
        nodes[node_id] = {
            "id": node_id,
            "label": label,
            "kind": kind,
            "score": score,
            "properties": properties,
        }

    def add_edge(
        source: str,
        target: str,
        kind: str,
        weight: float,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Insert one graph edge once, keyed by deterministic edge id."""
        edge_id = _edge_id(source, target, kind)
        if edge_id in edges:
            return
        edges[edge_id] = {
            "id": edge_id,
            "source": source,
            "target": target,
            "kind": kind,
            "weight": weight,
            "properties": properties or {},
        }

    for fm in selected:
        mid = str(fm.get("id", ""))
        memory_id = f"mem:{mid}"
        primitive = _detect_primitive(fm)
        tags = fm.get("tags", [])
        confidence = float(fm.get("confidence", 0.7))
        add_node(
            memory_id,
            label=str(fm.get("title", "")),
            kind="memory",
            score=confidence,
            properties={
                "memory_id": mid,
                "primitive": primitive,
                "tags": tags,
                "body_preview": str(fm.get("_body", "")).strip()[:480],
                "updated": str(fm.get("updated", "")),
            },
        )
        type_id = f"type:{primitive}"
        add_node(type_id, label=primitive, kind="type", score=0.4, properties={})
        add_edge(memory_id, type_id, "typed_as", 0.6)
        for tag in (tags or [])[:8]:
            tag_id = f"tag:{tag}"
            add_node(tag_id, label=str(tag), kind="tag", score=0.4, properties={})
            add_edge(memory_id, tag_id, "tagged", 0.55)

    memory_ids = [str(fm.get("id", "")) for fm in selected if fm.get("id")]
    for source_id, target_id, reason, score in _load_memory_graph_edges(
        memory_ids=memory_ids, limit=4000
    ):
        source = f"mem:{source_id}"
        target = f"mem:{target_id}"
        if source in nodes and target in nodes:
            add_edge(source, target, reason, score)

    node_values = list(nodes.values())
    edge_values = list(edges.values())
    truncated = False
    warnings: list[str] = []
    if len(node_values) > max_nodes:
        node_values = node_values[:max_nodes]
        allowed = {item["id"] for item in node_values}
        edge_values = [
            item
            for item in edge_values
            if item["source"] in allowed and item["target"] in allowed
        ]
        truncated = True
    if len(edge_values) > max_edges:
        edge_values = edge_values[:max_edges]
        truncated = True
    if truncated:
        warnings.append("Result truncated to requested node/edge limits.")

    return {
        "nodes": node_values,
        "edges": edge_values,
        "total_memories": matched_memories,
        "truncated": truncated,
        "warnings": warnings,
    }


def _memory_graph_query(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute memory graph query with filters and return bounded payload."""
    query = str(payload.get("query") or "")
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    max_nodes, max_edges = _graph_limits(
        payload, default_nodes=200, default_edges=3000, minimum_edges=100
    )

    type_values = _graph_filter_values(filters or {}, "type")
    tag_values = _graph_filter_values(filters or {}, "tags")

    all_items = _load_all_memories()
    selected = _filter_memories(
        all_items,
        query=query,
        type_filter=type_values[0] if type_values else None,
        state_filter=None,
        project_filter=None,
    )

    if type_values:
        allowed_types = set(type_values)
        selected = [
            item for item in selected if _detect_primitive(item) in allowed_types
        ]
    if tag_values:
        allowed_tags = set(tag_values)
        selected = [
            item for item in selected if allowed_tags.intersection(item.get("tags", []))
        ]

    matched_memories = len(selected)
    return _build_memory_graph_payload(
        selected=selected[:max_nodes],
        matched_memories=matched_memories,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )


def _memory_graph_expand(payload: dict[str, Any]) -> dict[str, Any]:
    """Expand one memory node by related/tag neighborhood."""
    node_id = str(payload.get("node_id") or "")
    max_nodes, max_edges = _graph_limits(
        payload, default_nodes=500, default_edges=1200, minimum_edges=50
    )
    if not node_id.startswith("mem:"):
        return {
            "nodes": [],
            "edges": [],
            "stats": {"added_nodes": 0, "added_edges": 0, "truncated": False},
            "warnings": ["Only memory node expansion is supported in this build."],
        }
    memory_id = node_id.split("mem:", 1)[1]
    all_items = _load_all_memories()
    seed = next((fm for fm in all_items if str(fm.get("id", "")) == memory_id), None)
    if seed is None:
        return {
            "nodes": [],
            "edges": [],
            "stats": {"added_nodes": 0, "added_edges": 0, "truncated": False},
            "warnings": ["Selected memory no longer exists."],
        }

    neighbor_ids: set[str] = {memory_id}
    seed_tags = set(seed.get("tags", []))
    for fm in all_items:
        fid = str(fm.get("id", ""))
        if fid == memory_id:
            continue
        if seed_tags.intersection(fm.get("tags", [])):
            neighbor_ids.add(fid)

    for source_id, target_id, _reason, _score in _load_memory_graph_edges(
        seed_memory_id=memory_id, limit=500
    ):
        if source_id == memory_id and target_id:
            neighbor_ids.add(target_id)
        if target_id == memory_id and source_id:
            neighbor_ids.add(source_id)

    focused = [fm for fm in all_items if str(fm.get("id", "")) in neighbor_ids]
    graph = _build_memory_graph_payload(
        selected=focused[:max_nodes],
        matched_memories=len(focused),
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    return {
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "stats": {
            "added_nodes": len(graph["nodes"]),
            "added_edges": len(graph["edges"]),
            "truncated": bool(graph.get("stats", {}).get("truncated")),
        },
        "warnings": graph.get("warnings", []),
    }


def _serialize_full_config(config: Config) -> dict[str, Any]:
    """Serialize full Config dataclass to a dashboard-friendly JSON dict."""

    def _role_dict(role: Any) -> dict[str, Any]:
        """Convert LLMRoleConfig or DSPyRoleConfig to a dict."""
        base: dict[str, Any] = {
            "provider": role.provider,
            "model": role.model,
            "api_base": getattr(role, "api_base", ""),
            "timeout_seconds": role.timeout_seconds,
            "max_iterations": role.max_iterations,
            "openrouter_provider_order": list(
                getattr(role, "openrouter_provider_order", ())
            ),
        }
        if hasattr(role, "fallback_models"):
            base["fallback_models"] = list(role.fallback_models)
        if hasattr(role, "sub_model"):
            base["sub_model"] = role.sub_model
        if hasattr(role, "sub_provider"):
            base["sub_provider"] = role.sub_provider
        if hasattr(role, "max_llm_calls"):
            base["max_llm_calls"] = role.max_llm_calls
        return base

    return {
        "server": {
            "host": config.server_host,
            "port": config.server_port,
            "poll_interval_minutes": config.poll_interval_minutes,
            "sync_window_days": config.sync_window_days,
            "sync_max_sessions": config.sync_max_sessions,
            "sync_max_workers": config.sync_max_workers,
        },
        "memory": {
            "scope": config.memory_scope,
            "dir": str(config.memory_dir),
        },
        "roles": {
            "lead": _role_dict(config.lead_role),
            "explorer": _role_dict(config.explorer_role),
            "extract": _role_dict(config.extract_role),
            "summarize": _role_dict(config.summarize_role),
        },
        "tracing": {
            "enabled": config.tracing_enabled,
            "include_httpx": config.tracing_include_httpx,
            "include_content": config.tracing_include_content,
        },
        "data_dir": str(config.data_dir),
    }


def _save_config_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply config patch to user config TOML file and return updated config."""
    config = save_config_patch(patch)
    return _serialize_full_config(config)


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for read-only dashboard APIs and static assets."""

    server_version = "LerimDashboard/0.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        """Route request logs through project logger."""
        logger.debug("dashboard | " + fmt, *args)

    def _json(self, payload: dict | list, status: int = HTTPStatus.OK) -> None:
        """Write JSON response with status code."""
        body = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        """Write standard JSON error payload."""
        self._json({"error": message}, status=status)

    def _read_json_body(self) -> dict[str, Any]:
        """Read and parse request body as a JSON object."""
        raw_len = self.headers.get("Content-Length")
        size = _parse_int(raw_len, 0, minimum=0, maximum=MAX_BODY_BYTES)
        if size <= 0:
            return {}
        body = self.rfile.read(size)
        if not body:
            return {}
        try:
            parsed = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _serve_file(self, relative_path: str) -> None:
        """Serve static dashboard asset with directory traversal protection."""
        path = (DASHBOARD_DIR / relative_path.lstrip("/")).resolve()
        if (
            not str(path).startswith(str(DASHBOARD_DIR.resolve()))
            or not path.exists()
            or not path.is_file()
        ):
            self._error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        raw = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _api_runs_stats(self, query: dict[str, list[str]]) -> None:
        """Return aggregate run stats for selected scope and agent filter."""
        scope = (query.get("scope") or ["week"])[0]
        agent = (query.get("agent_type") or ["all"])[0]
        since, until = _scope_bounds(scope)
        rows = _sqlite_rows(since, until, agent)
        self._json(_compute_stats(rows))

    def _api_runs(self, query: dict[str, list[str]]) -> None:
        """Return paginated run list for selected scope and agent filter."""
        scope = (query.get("scope") or ["week"])[0]
        agent = (query.get("agent_type") or ["all"])[0]
        limit = _parse_int(
            (query.get("limit") or ["30"])[0], 30, minimum=1, maximum=200
        )
        offset = _parse_int(
            (query.get("offset") or ["0"])[0], 0, minimum=0, maximum=10_000
        )
        since, until = _scope_bounds(scope)
        rows, total = list_sessions_window(
            limit=limit,
            offset=offset,
            agent_types=None if agent in {"", "all"} else [agent],
            since=since,
            until=until,
        )
        runs = [_serialize_run(row) for row in rows]
        self._json(
            {
                "runs": runs,
                "pagination": {
                    "offset": offset,
                    "total": total,
                    "has_more": (offset + len(runs)) < total,
                },
            }
        )

    def _api_search(self, query: dict[str, list[str]]) -> None:
        """Run FTS/keyword session search with optional filters and pagination."""
        config = get_config()
        run_query = (query.get("query") or [""])[0].strip()
        scope = (query.get("scope") or ["week"])[0]
        agent = (query.get("agent_type") or ["all"])[0]
        status_filter = (query.get("status") or [""])[0].strip()
        repo_filter = (query.get("repo") or [""])[0].strip()
        limit = _parse_int(
            (query.get("limit") or ["30"])[0], 30, minimum=1, maximum=200
        )
        offset = _parse_int(
            (query.get("offset") or ["0"])[0], 0, minimum=0, maximum=10_000
        )
        since, until = _scope_bounds(scope)
        where = []
        params: list[Any] = []
        if since is not None:
            where.append("(d.start_time >= ? OR d.start_time IS NULL)")
            params.append(since.isoformat())
        where.append("(d.start_time <= ? OR d.start_time IS NULL)")
        params.append(until.isoformat())
        if agent and agent != "all":
            where.append("d.agent_type = ?")
            params.append(agent)
        if status_filter:
            where.append("d.status = ?")
            params.append(status_filter)
        if repo_filter:
            where.append("d.repo_name LIKE ?")
            params.append(f"%{repo_filter}%")
        where_sql = (" AND " + " AND ".join(where)) if where else ""
        with sqlite3.connect(config.sessions_db_path) as conn:
            conn.row_factory = sqlite3.Row
            if run_query:
                search_sql = f"""\
SELECT d.run_id, d.agent_type, d.status, d.start_time, d.duration_ms, d.message_count, \
d.tool_call_count, d.error_count, d.total_tokens, d.repo_name, d.summary_text, \
snippet(sessions_fts, 3, '<mark>', '</mark>', '...', 24) AS snippet \
FROM sessions_fts JOIN session_docs d ON d.id = sessions_fts.rowid \
WHERE sessions_fts MATCH ?{where_sql} ORDER BY d.start_time DESC LIMIT ? OFFSET ?"""
                rows = conn.execute(
                    search_sql, [run_query, *params, limit, offset]
                ).fetchall()
                count_sql = f"""\
SELECT COUNT(1) AS total FROM sessions_fts JOIN session_docs d ON d.id = sessions_fts.rowid \
WHERE sessions_fts MATCH ?{where_sql}"""
                total = int(
                    conn.execute(count_sql, [run_query, *params]).fetchone()["total"]
                    or 0
                )
            else:
                search_sql = f"""\
SELECT d.run_id, d.agent_type, d.status, d.start_time, d.duration_ms, d.message_count, \
d.tool_call_count, d.error_count, d.total_tokens, d.repo_name, d.summary_text, d.summary_text AS snippet \
FROM session_docs d WHERE 1=1{where_sql} ORDER BY d.start_time DESC LIMIT ? OFFSET ?"""
                rows = conn.execute(search_sql, [*params, limit, offset]).fetchall()
                count_sql = f"""\
SELECT COUNT(1) AS total FROM session_docs d WHERE 1=1{where_sql}"""
                total = int(conn.execute(count_sql, params).fetchone()["total"] or 0)
        results = []
        for row in rows:
            run = _serialize_run(dict(row))
            run["snippet"] = row["snippet"] or run.get("snippet") or ""
            results.append(run)
        self._json(
            {
                "mode": "fts" if run_query else "keyword",
                "results": results,
                "pagination": {
                    "offset": offset,
                    "total": total,
                    "has_more": (offset + len(results)) < total,
                },
            }
        )

    def _api_run_messages(self, path: str) -> None:
        """Return normalized message timeline for one run id."""
        run_id = unquote(path.split("/api/runs/", 1)[1].rsplit("/messages", 1)[0])
        run_doc = fetch_session_doc(run_id)
        if run_doc is None:
            self._error(HTTPStatus.NOT_FOUND, "Run not found")
            return
        messages = _load_messages_for_run(run_doc)
        self._json({"messages": messages})

    def _api_memories(self, query: dict[str, list[str]]) -> None:
        """Return filtered memory list for dashboard memory explorer."""
        all_items = _load_all_memories()
        query_text = (query.get("query") or [""])[0].strip()
        type_filter = (query.get("type") or [""])[0].strip()
        state_filter = (query.get("state") or [""])[0].strip()
        project_filter = (query.get("project") or [""])[0].strip()
        items = _filter_memories(
            all_items,
            query=query_text,
            type_filter=type_filter or None,
            state_filter=state_filter or None,
            project_filter=project_filter or None,
        )
        self._json(
            {
                "items": [
                    _serialize_memory(item, with_body=False) for item in items[:300]
                ],
                "total": len(items),
                "summary": f"{len(items)} memories",
            }
        )

    def _api_memory_detail(self, path: str) -> None:
        """Return full memory details for one memory id."""
        memory_id = unquote(path.split("/api/memories/", 1)[1])
        all_items = _load_all_memories()
        fm = next(
            (item for item in all_items if str(item.get("id", "")) == memory_id), None
        )
        if fm is None:
            self._error(HTTPStatus.NOT_FOUND, "Memory not found")
            return
        self._json({"memory": _serialize_memory(fm, with_body=True)})

    def _api_memory_graph_options(self) -> None:
        """Return memory-graph filter option lists."""
        self._json(_memory_graph_options(_load_all_memories()))

    def _api_refine_status(self) -> None:
        """Return queue and recent run status for refine panel."""
        payload = {
            "queue": count_session_jobs_by_status(),
            "sync": latest_service_run("sync"),
            "maintain": latest_service_run("maintain"),
        }
        self._json(payload)

    def _api_refine_report(self) -> None:
        """Return cached or freshly built extraction quality report."""
        now = datetime.now(timezone.utc)
        cached_at = _REPORT_CACHE.get("at")
        if (
            isinstance(cached_at, datetime)
            and _REPORT_CACHE.get("value")
            and (now - cached_at).total_seconds() < 60
        ):
            self._json(_REPORT_CACHE["value"])
            return
        try:
            report = build_extract_report()
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"Report unavailable: {exc}")
            return
        _REPORT_CACHE["at"] = now
        _REPORT_CACHE["value"] = report
        self._json(report)

    def _api_config(self) -> None:
        """Return full effective runtime config used by dashboard."""
        config = get_config()
        payload = {
            "effective": _serialize_full_config(config),
            "sources": get_config_sources(),
            "user_config_path": str(get_user_config_path()),
            "read_only": False,
        }
        self._json(payload)

    def _api_config_models(self, query: dict[str, list[str]]) -> None:
        """Return available model list for the selected provider."""
        config = get_config()
        provider = (query.get("provider") or [config.provider])[0]
        models = sorted(set(list_provider_models(provider)))
        self._json({"models": models})

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        """Dispatch GET API routes to the matching handler."""
        query_handlers = {
            "/api/runs/stats": self._api_runs_stats,
            "/api/runs": self._api_runs,
            "/api/search": self._api_search,
            "/api/memories": self._api_memories,
            "/api/config/models": self._api_config_models,
        }
        no_query_handlers = {
            "/api/health": lambda: self._json(api_health()),
            "/api/status": lambda: self._json(api_status()),
            "/api/connect": lambda: self._json({"platforms": api_connect_list()}),
            "/api/project/list": lambda: self._json({"projects": api_project_list()}),
            "/api/memory-graph/options": self._api_memory_graph_options,
            "/api/refine/status": self._api_refine_status,
            "/api/refine/report": self._api_refine_report,
            "/api/config": self._api_config,
        }
        if path in query_handlers:
            query_handlers[path](query)
            return
        if path in no_query_handlers:
            no_query_handlers[path]()
            return
        if path.startswith("/api/runs/") and path.endswith("/messages"):
            self._api_run_messages(path)
            return
        if path.startswith("/api/memories/"):
            self._api_memory_detail(path)
            return
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def _api_config_save(self) -> None:
        """Save config patch to user config TOML file."""
        body = self._read_json_body()
        patch = body.get("patch")
        if not isinstance(patch, dict) or not patch:
            self._error(HTTPStatus.BAD_REQUEST, "Missing 'patch' object in body")
            return
        try:
            updated = _save_config_patch(patch)
            self._json(
                {
                    "effective": updated,
                    "sources": get_config_sources(),
                    "user_config_path": str(get_user_config_path()),
                }
            )
        except Exception as exc:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"Failed to save config: {exc}",
            )

    def _handle_api_post(self, path: str) -> None:
        """Dispatch POST API routes to supported actions."""
        if path == "/api/chat":
            body = self._read_json_body()
            question = str(body.get("question") or "").strip()
            if not question:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'question'")
                return
            limit = int(body.get("limit") or 12)
            import threading

            result_holder: list[dict[str, Any]] = []

            def _run_chat() -> None:
                """Execute chat in background thread."""
                result_holder.append(api_chat(question, limit=limit))

            thread = threading.Thread(target=_run_chat)
            thread.start()
            thread.join(timeout=300)
            if result_holder:
                self._json(result_holder[0])
            else:
                self._error(HTTPStatus.GATEWAY_TIMEOUT, "Chat timed out")
            return
        if path == "/api/sync":
            body = self._read_json_body()
            import threading
            import uuid

            job_id = str(uuid.uuid4())[:8]

            def _run_sync() -> None:
                """Execute sync in background."""
                api_sync(
                    agent=body.get("agent"),
                    window=body.get("window", "7d"),
                    max_sessions=body.get("max_sessions"),
                    force=bool(body.get("force")),
                    dry_run=bool(body.get("dry_run")),
                )

            threading.Thread(
                target=_run_sync, name=f"sync-{job_id}", daemon=True
            ).start()
            self._json({"status": "started", "job_id": job_id})
            return
        if path == "/api/maintain":
            body = self._read_json_body()
            import threading
            import uuid

            job_id = str(uuid.uuid4())[:8]

            def _run_maintain() -> None:
                """Execute maintain in background."""
                api_maintain(
                    force=bool(body.get("force")),
                    dry_run=bool(body.get("dry_run")),
                )

            threading.Thread(
                target=_run_maintain, name=f"maintain-{job_id}", daemon=True
            ).start()
            self._json({"status": "started", "job_id": job_id})
            return
        if path == "/api/connect":
            body = self._read_json_body()
            platform = str(body.get("platform") or "").strip()
            if not platform:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'platform'")
                return
            result = api_connect(platform, path=body.get("path"))
            self._json(result)
            return
        if path == "/api/project/add":
            body = self._read_json_body()
            proj_path = str(body.get("path") or "").strip()
            if not proj_path:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'path'")
                return
            result = api_project_add(proj_path)
            status_code = (
                HTTPStatus.BAD_REQUEST if result.get("error") else HTTPStatus.OK
            )
            self._json(result, status=status_code)
            return
        if path == "/api/project/remove":
            body = self._read_json_body()
            name = str(body.get("name") or "").strip()
            if not name:
                self._error(HTTPStatus.BAD_REQUEST, "Missing 'name'")
                return
            result = api_project_remove(name)
            status_code = (
                HTTPStatus.BAD_REQUEST if result.get("error") else HTTPStatus.OK
            )
            self._json(result, status=status_code)
            return
        if path == "/api/memory-graph/query":
            payload = self._read_json_body()
            self._json(_memory_graph_query(payload))
            return
        if path == "/api/memory-graph/expand":
            payload = self._read_json_body()
            self._json(_memory_graph_expand(payload))
            return
        if path == "/api/config":
            self._api_config_save()
            return
        if path in {"/api/refine/run", "/api/reflect"}:
            self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)
            return
        self._error(HTTPStatus.NOT_FOUND, "Not found")

    def do_GET(self) -> None:  # noqa: N802
        """Serve API routes and static dashboard assets for GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path or "/"
        query = parse_qs(parsed.query or "", keep_blank_values=True)
        if path.startswith("/api/"):
            self._handle_api_get(path, query)
            return
        if path == "/" or path == "/index.html":
            self._serve_file("index.html")
            return
        if path.startswith("/session/"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/?tab=runs")
            self.end_headers()
            return
        self._serve_file(path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802
        """Serve read-only POST API endpoints."""
        parsed = urlparse(self.path)
        self._handle_api_post(parsed.path or "/")

    def do_PUT(self) -> None:  # noqa: N802
        """Reject mutating PUT requests in read-only dashboard mode."""
        self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)

    def do_PATCH(self) -> None:  # noqa: N802
        """Handle PATCH requests - config save allowed, others rejected."""
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self._api_config_save()
            return
        self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)

    def do_DELETE(self) -> None:  # noqa: N802
        """Reject mutating DELETE requests in read-only dashboard mode."""
        self._error(HTTPStatus.FORBIDDEN, READ_ONLY_MESSAGE)


def run_dashboard_server(host: str | None = None, port: int | None = None) -> int:
    """Run read-only dashboard HTTP server."""
    config = get_config()
    bind_host = host or config.server_host or "127.0.0.1"
    bind_port = int(port or config.server_port or 8765)
    init_sessions_db()
    httpd = ThreadingHTTPServer((bind_host, bind_port), DashboardHandler)
    logger.info("Lerim dashboard running at http://{}:{}/", bind_host, bind_port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down dashboard server")
        httpd.shutdown()
    return 0
