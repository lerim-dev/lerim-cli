"""Cloud shipper — ships local data (logs, sessions, memories) to lerim-cloud.

Reads new entries from local storage, batches them, and POSTs to the cloud API.
Tracks shipping offsets in ``~/.lerim/cloud_shipper_state.json`` to avoid
re-sending.  Designed to run inside the daemon loop via ``ship_once()``.

Uses only stdlib ``urllib.request`` for HTTP — no third-party HTTP deps.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lerim.config.logging import LOG_DIR, logger
from lerim.config.settings import Config

# ── constants ────────────────────────────────────────────────────────────────

_STATE_PATH = Path.home() / ".lerim" / "cloud_shipper_state.json"

_BATCH_LOGS = 500
_BATCH_SESSIONS = 100
_BATCH_MEMORIES = 100

_HTTP_TIMEOUT_SECONDS = 30
_GZIP_THRESHOLD_BYTES = 1024


# ── state persistence ────────────────────────────────────────────────────────


@dataclass
class _ShipperState:
    """Mutable shipping-offset state persisted between daemon cycles."""

    log_offset_bytes: int = 0
    log_file: str = "lerim.jsonl"
    sessions_shipped_at: str = ""
    memories_shipped_at: str = ""
    memories_pulled_at: str = ""
    service_runs_shipped_at: str = ""

    def save(self) -> None:
        """Write state to disk."""
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(asdict(self), ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> "_ShipperState":
        """Load state from disk, returning defaults on any error."""
        try:
            raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return cls()
            return cls(
                log_offset_bytes=int(raw.get("log_offset_bytes") or 0),
                log_file=str(raw.get("log_file") or "lerim.jsonl"),
                sessions_shipped_at=str(raw.get("sessions_shipped_at") or ""),
                memories_shipped_at=str(raw.get("memories_shipped_at") or ""),
                memories_pulled_at=str(raw.get("memories_pulled_at") or ""),
                service_runs_shipped_at=str(raw.get("service_runs_shipped_at") or ""),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return cls()


# ── HTTP helper ──────────────────────────────────────────────────────────────


def _post_batch_sync(
    endpoint: str, path: str, token: str, payload: dict[str, Any]
) -> bool:
    """POST a JSON payload to the cloud API.  Returns ``True`` on 2xx.

    Compresses the body with gzip when it exceeds ``_GZIP_THRESHOLD_BYTES``.
    This is a *synchronous* call — callers wrap it with ``asyncio.to_thread``.
    """
    url = f"{endpoint.rstrip('/')}{path}"
    body = json.dumps(payload, ensure_ascii=True, default=str).encode("utf-8")

    headers: dict[str, str] = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Note: gzip disabled — FastAPI does not decompress Content-Encoding: gzip
    # by default.  Re-enable once the cloud API adds gzip middleware.

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        logger.warning("cloud POST {} failed: {} — {}", path, exc, body_text)
        return False
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("cloud POST {} failed: {}", path, exc)
        return False


async def _post_batch(
    endpoint: str, path: str, token: str, payload: dict[str, Any]
) -> bool:
    """Async wrapper that offloads the synchronous HTTP call to a thread."""
    return await asyncio.to_thread(_post_batch_sync, endpoint, path, token, payload)


# ── HTTP GET helper ──────────────────────────────────────────────────────────


def _get_json_sync(
    endpoint: str, path: str, token: str, params: dict[str, str]
) -> dict[str, Any] | None:
    """Synchronous GET request returning parsed JSON."""
    qs = "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    url = f"{endpoint.rstrip('/')}{path}{'?' + qs if qs else ''}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        logger.warning("cloud GET {} failed: {}", path, exc)
        return None


# ── pull helpers ─────────────────────────────────────────────────────────────


def _find_memory_file(project_path: Path, memory_id: str) -> Path | None:
    """Find an existing local memory file by its ID."""
    memory_root = project_path / ".lerim" / "memory"
    if not memory_root.is_dir():
        return None
    for md_path in memory_root.rglob("*.md"):
        try:
            text = md_path.read_text(encoding="utf-8")
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    import yaml

                    fm = yaml.safe_load(text[3:end]) or {}
                    if fm.get("id") == memory_id:
                        return md_path
        except Exception:
            continue
    return None


async def _pull_memories(
    endpoint: str, token: str, config: Config, state: _ShipperState
) -> int:
    """Pull dashboard-edited memories from cloud and write to local files."""
    params: dict[str, str] = {"limit": "200"}
    if state.memories_pulled_at:
        params["since"] = state.memories_pulled_at

    try:
        data = await asyncio.to_thread(
            _get_json_sync, endpoint, "/api/v1/sync/memories", token, params
        )
    except Exception as exc:
        logger.warning("cloud pull memories failed: {}", exc)
        return 0

    if not data or not data.get("memories"):
        return 0

    pulled = 0
    latest_edited = state.memories_pulled_at

    for mem in data["memories"]:
        cloud_edited = mem.get("cloud_edited_at", "")
        if not cloud_edited:
            continue

        # Find the project directory for this memory
        project_name = mem.get("project")
        if not project_name or project_name not in (config.projects or {}):
            # Try first project as fallback
            if config.projects:
                project_name = next(iter(config.projects))
            else:
                continue

        project_path = Path(config.projects[project_name])
        memory_type = mem.get("memory_type", "decision")
        memory_dir = project_path / ".lerim" / "memory" / f"{memory_type}s"
        memory_dir.mkdir(parents=True, exist_ok=True)

        # Build filename from memory_id
        memory_id = mem.get("memory_id", "")
        if not memory_id:
            continue

        # Find existing file or create new one
        existing_file = _find_memory_file(project_path, memory_id)
        if existing_file:
            target_path = existing_file
        else:
            # Create new file: YYYYMMDD-slug.md
            from datetime import datetime, timezone

            date_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")
            slug = memory_id[:60].replace(" ", "-").lower()
            target_path = memory_dir / f"{date_prefix}-{slug}.md"

        # Build frontmatter + body
        frontmatter: dict[str, Any] = {
            "id": memory_id,
            "title": mem.get("title", ""),
            "confidence": mem.get("confidence"),
            "tags": mem.get("tags", []),
            "source": mem.get("source", "cloud-edit"),
            "created": mem.get("created_at", ""),
            "updated": cloud_edited,
        }
        # Remove None values
        frontmatter = {k: v for k, v in frontmatter.items() if v is not None}

        body = mem.get("body", "")

        # Write YAML frontmatter + markdown body
        import yaml

        fm_str = yaml.dump(
            frontmatter, default_flow_style=False, allow_unicode=True
        ).strip()
        content = f"---\n{fm_str}\n---\n\n{body}\n"

        try:
            target_path.write_text(content, encoding="utf-8")
            pulled += 1
        except OSError as exc:
            logger.warning("failed to write pulled memory {}: {}", memory_id, exc)

        if cloud_edited > latest_edited:
            latest_edited = cloud_edited

    if latest_edited and latest_edited != state.memories_pulled_at:
        state.memories_pulled_at = latest_edited

    return pulled


# ── log shipping ─────────────────────────────────────────────────────────────


async def _ship_logs(endpoint: str, token: str, state: _ShipperState) -> int:
    """Ship new log entries from ``lerim.jsonl`` since last offset.

    Handles log rotation: if the file is smaller than the stored offset the
    offset is reset to zero.
    """
    log_path = LOG_DIR / state.log_file
    if not log_path.exists():
        return 0

    file_size = log_path.stat().st_size
    offset = state.log_offset_bytes

    # Detect rotation: file shrunk below our bookmark.
    if file_size < offset:
        logger.info("cloud shipper: log file rotated, resetting offset")
        offset = 0

    if offset >= file_size:
        return 0

    shipped = 0
    new_offset = offset
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            fh.seek(offset)
            batch: list[dict[str, Any]] = []
            while True:
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                batch.append(entry)

                if len(batch) >= _BATCH_LOGS:
                    ok = await _post_batch(
                        endpoint,
                        "/api/v1/ingest/logs",
                        token,
                        {"entries": batch},
                    )
                    if ok:
                        shipped += len(batch)
                    else:
                        # Stop shipping on first failure; resume next cycle.
                        new_offset = fh.tell()
                        state.log_offset_bytes = new_offset
                        return shipped
                    batch = []

            # Flush remaining partial batch.
            if batch:
                ok = await _post_batch(
                    endpoint,
                    "/api/v1/ingest/logs",
                    token,
                    {"entries": batch},
                )
                if ok:
                    shipped += len(batch)

            new_offset = fh.tell()
    except OSError as exc:
        logger.warning("cloud shipper: failed reading log file: {}", exc)

    state.log_offset_bytes = new_offset
    return shipped


# ── session shipping ─────────────────────────────────────────────────────────


def _query_new_sessions(
    db_path: Path, since_iso: str, limit: int
) -> list[dict[str, Any]]:
    """Query sessions with ``indexed_at`` after *since_iso* (synchronous)."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = lambda cur, row: {
            col[0]: row[idx] for idx, col in enumerate(cur.description)
        }
        if since_iso:
            rows = conn.execute(
                """
                SELECT run_id, agent_type, repo_path, repo_name, start_time,
                       indexed_at, status, duration_ms, message_count,
                       tool_call_count, error_count, total_tokens,
                       summary_text, tags, outcome, session_path
                FROM session_docs
                WHERE indexed_at > ?
                ORDER BY indexed_at ASC
                LIMIT ?
                """,
                (since_iso, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT run_id, agent_type, repo_path, repo_name, start_time,
                       indexed_at, status, duration_ms, message_count,
                       tool_call_count, error_count, total_tokens,
                       summary_text, tags, outcome, session_path
                FROM session_docs
                ORDER BY indexed_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error as exc:
        logger.warning("cloud shipper: session query failed: {}", exc)
        return []


def _read_transcript(session_path: str | None) -> str | None:
    """Read the cached JSONL transcript file for a session, if it exists."""
    if not session_path:
        return None
    try:
        p = Path(session_path).expanduser()
        if p.is_file() and p.stat().st_size < 5_000_000:  # skip files > 5MB
            return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return None


async def _ship_sessions(
    endpoint: str, token: str, state: _ShipperState, db_path: Path
) -> int:
    """Ship new/updated sessions from SQLite, including cached transcripts."""
    shipped = 0
    latest_indexed_at = state.sessions_shipped_at

    while True:
        rows = await asyncio.to_thread(
            _query_new_sessions, db_path, latest_indexed_at, _BATCH_SESSIONS
        )
        if not rows:
            break

        # Map SQLite rows to the API's expected SessionEntry fields
        _API_FIELDS = {
            "run_id", "agent_type", "repo_name", "start_time",
            "duration_ms", "message_count", "tool_call_count",
            "error_count", "total_tokens", "summary_text",
            "project", "machine_id", "transcript_jsonl",
        }
        sessions_payload = []
        for row in rows:
            entry = {k: v for k, v in row.items() if k in _API_FIELDS and v is not None}
            # Attach transcript from cached JSONL file
            transcript = _read_transcript(row.get("session_path"))
            if transcript:
                entry["transcript_jsonl"] = transcript
            # Use repo_path as project fallback
            if "project" not in entry and row.get("repo_path"):
                entry["project"] = Path(row["repo_path"]).name
            sessions_payload.append(entry)

        ok = await _post_batch(
            endpoint,
            "/api/v1/ingest/sessions",
            token,
            {"sessions": sessions_payload},
        )
        if ok:
            shipped += len(rows)
            # Advance watermark to the latest indexed_at in this batch.
            last_row_ts = str(rows[-1].get("indexed_at") or "")
            if last_row_ts > latest_indexed_at:
                latest_indexed_at = last_row_ts
        else:
            break

        # If we got fewer than a full batch, we have caught up.
        if len(rows) < _BATCH_SESSIONS:
            break

    if latest_indexed_at and latest_indexed_at != state.sessions_shipped_at:
        state.sessions_shipped_at = latest_indexed_at
    return shipped


# ── memory shipping ──────────────────────────────────────────────────────────


def _scan_memory_files(
    projects: dict[str, str], since_iso: str
) -> list[dict[str, Any]]:
    """Scan project memory directories for files updated after *since_iso*.

    Memory files live at ``<project_path>/.lerim/memory/{type}/*.md``.
    Each file is a frontmatter+markdown document.  We extract the ``updated``
    field from the frontmatter to compare against the watermark.

    Returns a list of dicts ready for JSON serialization.
    """
    import re

    results: list[dict[str, Any]] = []
    for project_name, project_path_str in projects.items():
        memory_root = Path(project_path_str) / ".lerim" / "memory"
        if not memory_root.is_dir():
            continue
        for md_path in memory_root.rglob("*.md"):
            if not md_path.is_file():
                continue
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue

            # Parse frontmatter with YAML (handles lists like tags properly).
            updated_value = ""
            frontmatter_raw: dict[str, Any] = {}
            if text.startswith("---"):
                end = text.find("---", 3)
                if end > 0:
                    fm_block = text[3:end].strip()
                    body = text[end + 3 :].strip()
                    try:
                        import yaml
                        frontmatter_raw = yaml.safe_load(fm_block) or {}
                    except Exception:
                        frontmatter_raw = {}
                    updated_value = str(frontmatter_raw.get("updated", ""))
                    # Strip ARCHIVED prefix from body (added by maintain process)
                    if body.startswith("ARCHIVED"):
                        body_lines = body.split("\n", 2)
                        body = body_lines[2].strip() if len(body_lines) > 2 else body_lines[-1].strip()
                else:
                    body = text
            else:
                body = text

            # Filter by watermark.
            if since_iso and updated_value and updated_value <= since_iso:
                continue

            # Determine memory type and archived status from folder structure.
            _TYPE_MAP = {"decisions": "decision", "learnings": "learning", "summaries": "summary"}
            rel = md_path.relative_to(memory_root)
            parts = rel.parts
            is_archived_folder = len(parts) >= 2 and parts[0] == "archived"
            if is_archived_folder:
                raw_type = parts[1] if len(parts) > 1 else "unknown"
            else:
                raw_type = parts[0] if parts else "unknown"
            memory_type = _TYPE_MAP.get(raw_type, raw_type)

            results.append(
                {
                    "project": project_name,
                    "memory_type": memory_type,
                    "file": str(rel),
                    "frontmatter": frontmatter_raw,
                    "body": body,
                    "updated": updated_value,
                    "is_archived": is_archived_folder or bool(frontmatter_raw.get("archived")),
                }
            )
    return results


async def _ship_memories(
    endpoint: str,
    token: str,
    config: Config,
    state: _ShipperState,
) -> int:
    """Ship new/updated memories from project memory directories."""
    projects = config.projects or {}
    if not projects:
        return 0

    all_memories = await asyncio.to_thread(
        _scan_memory_files, projects, state.memories_shipped_at
    )
    if not all_memories:
        return 0

    shipped = 0
    latest_updated = state.memories_shipped_at

    for i in range(0, len(all_memories), _BATCH_MEMORIES):
        raw_batch = all_memories[i : i + _BATCH_MEMORIES]
        # Map scanned memory files to API's expected MemoryEntry fields
        batch = []
        for mem in raw_batch:
            fm = mem.get("frontmatter") or {}
            entry: dict[str, Any] = {
                "memory_id": fm.get("id") or mem.get("file", ""),
                "memory_type": mem.get("memory_type"),
                "title": fm.get("title", ""),
                "body": mem.get("body", ""),
                "project": mem.get("project"),
                "tags": fm.get("tags", []) if isinstance(fm.get("tags"), list) else [],
                "confidence": fm.get("confidence"),
                "source": fm.get("source"),
                "status": "archived" if mem.get("is_archived") else fm.get("status", "active"),
            }
            batch.append(entry)
        ok = await _post_batch(
            endpoint,
            "/api/v1/ingest/memories",
            token,
            {"memories": batch},
        )
        if ok:
            shipped += len(batch)
            for mem in batch:
                ts = str(mem.get("updated") or "")
                if ts > latest_updated:
                    latest_updated = ts
        else:
            break

    if latest_updated and latest_updated != state.memories_shipped_at:
        state.memories_shipped_at = latest_updated
    return shipped


# ── service-run shipping ─────────────────────────────────────────────────


def _query_service_runs(db_path: Path, since_iso: str, limit: int) -> list[dict[str, Any]]:
    """Query local service_runs table for new entries."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = lambda cur, row: {col[0]: row[idx] for idx, col in enumerate(cur.description)}
        if since_iso:
            rows = conn.execute(
                "SELECT job_type, status, started_at, completed_at, trigger, details_json "
                "FROM service_runs WHERE started_at > ? ORDER BY started_at ASC LIMIT ?",
                (since_iso, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT job_type, status, started_at, completed_at, trigger, details_json "
                "FROM service_runs ORDER BY started_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        # Parse details_json from string to dict
        for row in rows:
            dj = row.get("details_json")
            if isinstance(dj, str):
                try:
                    row["details_json"] = json.loads(dj)
                except json.JSONDecodeError:
                    row["details_json"] = {}
            elif dj is None:
                row["details_json"] = {}
        return rows
    except sqlite3.Error as exc:
        logger.warning("cloud shipper: service_runs query failed: {}", exc)
        return []


async def _ship_service_runs(
    endpoint: str, token: str, state: _ShipperState, db_path: Path
) -> int:
    """Ship service run records from local SQLite."""
    shipped = 0
    latest_started = state.service_runs_shipped_at

    while True:
        rows = await asyncio.to_thread(
            _query_service_runs, db_path, latest_started, 100
        )
        if not rows:
            break

        ok = await _post_batch(
            endpoint, "/api/v1/ingest/service_runs", token, {"runs": rows}
        )
        if ok:
            shipped += len(rows)
            last_ts = str(rows[-1].get("started_at") or "")
            if last_ts > latest_started:
                latest_started = last_ts
        else:
            break

        if len(rows) < 100:
            break

    if latest_started and latest_started != state.service_runs_shipped_at:
        state.service_runs_shipped_at = latest_started
    return shipped


# ── public entry point ───────────────────────────────────────────────────────


def _is_cloud_configured(config: Config) -> bool:
    """Check if cloud token and endpoint are both set."""
    return bool(config.cloud_token and config.cloud_endpoint)


async def ship_once(config: Config) -> dict[str, int]:
    """Run one sync cycle (pull then push).

    Returns counts of items synced per type, or an empty dict if cloud
    is not configured.
    """
    if not _is_cloud_configured(config):
        return {}

    endpoint = config.cloud_endpoint
    token = config.cloud_token or ""
    state = _ShipperState.load()

    # Phase 1: Pull (cloud -> local)
    memories_pulled = await _pull_memories(endpoint, token, config, state)

    # Phase 2: Push (local -> cloud)
    logs_shipped = await _ship_logs(endpoint, token, state)
    sessions_shipped = await _ship_sessions(
        endpoint, token, state, config.sessions_db_path
    )
    service_runs_shipped = await _ship_service_runs(
        endpoint, token, state, config.sessions_db_path
    )
    memories_shipped = await _ship_memories(endpoint, token, config, state)

    state.save()

    return {
        "logs": logs_shipped,
        "sessions": sessions_shipped,
        "service_runs": service_runs_shipped,
        "memories": memories_shipped,
        "memories_pulled": memories_pulled,
    }
