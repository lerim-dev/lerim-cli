"""Session catalog + durable queue for Lerim 004 core runtime."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from typing import Any

from lerim.adapters import registry as adapter_registry
from lerim.config.logging import logger
from lerim.config.settings import get_config, reload_config


JOB_TYPE_EXTRACT = "extract"
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_DEAD_LETTER = "dead_letter"
SESSION_JOB_TERMINAL = {JOB_STATUS_DONE, JOB_STATUS_DEAD_LETTER}
SESSION_JOB_ACTIVE = {JOB_STATUS_PENDING, JOB_STATUS_RUNNING}
_DB_INIT_LOCK = threading.Lock()
_DB_INITIALIZED_PATH: Path | None = None


@dataclass(frozen=True)
class IndexedSession:
    """Minimal indexed-session payload returned by ``index_new_sessions``."""

    run_id: str
    agent_type: str
    session_path: str
    start_time: str | None


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    """Return current UTC datetime as ISO8601 text."""
    return _utc_now().isoformat()


def _to_iso(value: datetime | None) -> str | None:
    """Convert datetime to UTC-aware ISO string when value is present."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _db_path() -> Path:
    """Return the configured SQLite path for session catalog storage."""
    return get_config().sessions_db_path


def _dict_row(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert SQLite row tuples into dictionary rows."""
    return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


def _connect() -> sqlite3.Connection:
    """Open catalog SQLite connection with dictionary row factory."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = _dict_row
    return conn


def _ensure_sessions_db_initialized() -> None:
    """Initialize schema once per resolved database path."""
    global _DB_INITIALIZED_PATH
    path = _db_path()
    if _DB_INITIALIZED_PATH == path and path.exists():
        return
    with _DB_INIT_LOCK:
        path = _db_path()
        if _DB_INITIALIZED_PATH == path and path.exists():
            return
        init_sessions_db()


def init_sessions_db() -> None:
    """Create/upgrade session catalog, queue, and service-run tables."""
    global _DB_INITIALIZED_PATH
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_docs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                agent_type TEXT NOT NULL,
                repo_path TEXT,
                repo_name TEXT,
                start_time TEXT,
                content TEXT,
                indexed_at TEXT NOT NULL,
                status TEXT DEFAULT 'completed',
                duration_ms INTEGER DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                summaries TEXT,
                summary_text TEXT,
                turns_json TEXT,
                session_path TEXT,
                tags TEXT,
                outcome TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_docs_run ON session_docs (run_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_docs_agent ON session_docs (agent_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_docs_time ON session_docs (start_time)"
        )

        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS sessions_fts USING fts5(
                run_id,
                agent_type,
                repo_name,
                content,
                content='session_docs',
                content_rowid='id'
            )
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS session_docs_ai AFTER INSERT ON session_docs BEGIN
                INSERT INTO sessions_fts(rowid, run_id, agent_type, repo_name, content)
                VALUES (new.id, new.run_id, new.agent_type, new.repo_name, new.content);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS session_docs_ad AFTER DELETE ON session_docs BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, run_id, agent_type, repo_name, content)
                VALUES ('delete', old.id, old.run_id, old.agent_type, old.repo_name, old.content);
            END
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS session_docs_au AFTER UPDATE ON session_docs BEGIN
                INSERT INTO sessions_fts(sessions_fts, rowid, run_id, agent_type, repo_name, content)
                VALUES ('delete', old.id, old.run_id, old.agent_type, old.repo_name, old.content);
                INSERT INTO sessions_fts(rowid, run_id, agent_type, repo_name, content)
                VALUES (new.id, new.run_id, new.agent_type, new.repo_name, new.content);
            END
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS session_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                job_type TEXT NOT NULL DEFAULT 'extract',
                agent_type TEXT,
                session_path TEXT,
                start_time TEXT,
                status TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                max_attempts INTEGER DEFAULT 3,
                trigger TEXT,
                available_at TEXT NOT NULL,
                claimed_at TEXT,
                completed_at TEXT,
                heartbeat_at TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(run_id, job_type)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_jobs_status_available ON session_jobs (status, available_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_jobs_updated ON session_jobs (updated_at)"
        )
        columns = {
            str(row["name"]): str(row["type"])
            for row in conn.execute("PRAGMA table_info(session_jobs)").fetchall()
        }
        additive_columns = {
            "job_type": "TEXT NOT NULL DEFAULT 'extract'",
            "agent_type": "TEXT",
            "session_path": "TEXT",
            "start_time": "TEXT",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "attempts": "INTEGER DEFAULT 0",
            "max_attempts": "INTEGER DEFAULT 3",
            "trigger": "TEXT",
            "available_at": "TEXT",
            "claimed_at": "TEXT",
            "completed_at": "TEXT",
            "heartbeat_at": "TEXT",
            "error": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        }
        for name, ddl in additive_columns.items():
            if name in columns:
                continue
            conn.execute(f"ALTER TABLE session_jobs ADD COLUMN {name} {ddl}")
        # Backfill non-null-ish defaults for older rows.
        now = _iso_now()
        conn.execute(
            "UPDATE session_jobs SET job_type = ? WHERE job_type IS NULL OR job_type = ''",
            (JOB_TYPE_EXTRACT,),
        )
        conn.execute(
            "UPDATE session_jobs SET status = ? WHERE status IS NULL OR status = ''",
            (JOB_STATUS_PENDING,),
        )
        conn.execute(
            "UPDATE session_jobs SET available_at = ? WHERE available_at IS NULL OR available_at = ''",
            (now,),
        )
        conn.execute(
            "UPDATE session_jobs SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
            (now,),
        )
        conn.execute(
            "UPDATE session_jobs SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''",
            (now,),
        )
        conn.execute(
            "UPDATE session_jobs SET status = ? WHERE status = 'queued'",
            (JOB_STATUS_PENDING,),
        )
        conn.execute(
            "UPDATE session_jobs SET status = ? WHERE status = 'completed'",
            (JOB_STATUS_DONE,),
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                trigger TEXT,
                details_json TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_runs_job ON service_runs (job_type)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_service_runs_started ON service_runs (started_at)"
        )
        conn.commit()
    _DB_INITIALIZED_PATH = _db_path()


def index_session_for_fts(
    run_id: str,
    agent_type: str,
    content: str,
    repo_path: str | None = None,
    repo_name: str | None = None,
    start_time: str | None = None,
    status: str = "completed",
    duration_ms: int = 0,
    message_count: int = 0,
    tool_call_count: int = 0,
    error_count: int = 0,
    total_tokens: int = 0,
    summaries: str | None = None,
    summary_text: str | None = None,
    turns_json: str | None = None,
    session_path: str | None = None,
) -> bool:
    """Insert or replace one session document row and keep FTS index synced."""
    if not run_id or not agent_type:
        return False
    _ensure_sessions_db_initialized()

    if summary_text is None and summaries:
        try:
            parsed = json.loads(summaries)
        except (json.JSONDecodeError, TypeError):
            parsed = []
        if isinstance(parsed, list):
            summary_text = "\n".join(str(item) for item in parsed if item)

    try:
        with _connect() as conn:
            conn.execute("DELETE FROM session_docs WHERE run_id = ?", (run_id,))
            conn.execute(
                """
                INSERT INTO session_docs (
                    run_id, agent_type, repo_path, repo_name, start_time, content,
                    indexed_at, status, duration_ms, message_count, tool_call_count,
                    error_count, total_tokens, summaries, summary_text, turns_json, session_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    agent_type,
                    repo_path,
                    repo_name,
                    start_time,
                    content,
                    _iso_now(),
                    status,
                    duration_ms,
                    message_count,
                    tool_call_count,
                    error_count,
                    total_tokens,
                    summaries,
                    summary_text,
                    turns_json,
                    session_path,
                ),
            )
            conn.commit()
        return True
    except sqlite3.Error as exc:
        logger.warning("session index failed | run_id={} error={}", run_id, str(exc))
        return False


def fetch_session_doc(run_id: str) -> dict[str, Any] | None:
    """Fetch one indexed session document by run id."""
    if not run_id:
        return None
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM session_docs WHERE run_id = ?", (run_id,)
        ).fetchone()
    return row if isinstance(row, dict) else None


def update_session_extract_fields(
    run_id: str,
    summary_text: str | None = None,
    tags: str | None = None,
    outcome: str | None = None,
) -> bool:
    """Update extraction-derived fields for one indexed session row."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()

    updates: list[str] = []
    params: list[Any] = []
    if summary_text is not None:
        updates.append("summary_text = ?")
        params.append(summary_text)
    if tags is not None:
        updates.append("tags = ?")
        params.append(tags)
    if outcome is not None:
        updates.append("outcome = ?")
        params.append(outcome)
    if not updates:
        return False

    params.append(run_id)
    with _connect() as conn:
        cursor = conn.execute(
            f"UPDATE session_docs SET {', '.join(updates)} WHERE run_id = ?", params
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def count_fts_indexed() -> int:
    """Return count of indexed session documents."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(1) AS total FROM session_docs").fetchone()
    return int((row or {}).get("total") or 0)


def get_indexed_run_ids() -> set[str]:
    """Return the set of run IDs already indexed in session docs."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute("SELECT run_id FROM session_docs").fetchall()
    return {str(row.get("run_id")) for row in rows if row.get("run_id")}


def list_sessions_window(
    *,
    limit: int = 100,
    offset: int = 0,
    agent_types: list[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """List sessions in a filtered window plus total row count."""
    _ensure_sessions_db_initialized()
    where: list[str] = []
    params: list[Any] = []

    if agent_types:
        placeholders = ",".join("?" for _ in agent_types)
        where.append(f"agent_type IN ({placeholders})")
        params.extend(agent_types)
    if since is not None:
        where.append("start_time >= ?")
        params.append(_to_iso(since))
    if until is not None:
        where.append("start_time <= ?")
        params.append(_to_iso(until))

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, int(limit))
    offset = max(0, int(offset))

    with _connect() as conn:
        total_row = conn.execute(
            f"SELECT COUNT(1) AS total FROM session_docs {where_sql}",
            params,
        ).fetchone()
        rows = conn.execute(
            f"""
            SELECT *
            FROM session_docs
            {where_sql}
            ORDER BY start_time DESC, indexed_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
    return rows, int((total_row or {}).get("total") or 0)


def list_sessions_for_vectors(limit: int = 2000) -> list[dict[str, Any]]:
    """Return recent sessions with summary text for optional vector indexing."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT run_id, summary_text, agent_type, start_time
            FROM session_docs
            WHERE summary_text IS NOT NULL AND summary_text != ''
            ORDER BY start_time DESC, indexed_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return rows


def index_new_sessions(
    *,
    agents: list[str] | None = None,
    return_details: bool = False,
    start: datetime | None = None,
    end: datetime | None = None,
) -> int | list[IndexedSession]:
    """Discover and index new sessions from connected adapters."""
    _ensure_sessions_db_initialized()
    config = get_config()

    connected_paths = adapter_registry.get_connected_platform_paths(
        config.platforms_path
    )
    selected_agents = agents or adapter_registry.get_connected_agents(
        config.platforms_path
    )
    indexed_run_ids = get_indexed_run_ids()

    new_sessions: list[IndexedSession] = []

    for agent_name in selected_agents:
        adapter = adapter_registry.get_adapter(agent_name)
        traces_dir = connected_paths.get(agent_name)
        if adapter is None or traces_dir is None:
            continue

        try:
            sessions = adapter.iter_sessions(
                traces_dir=traces_dir,
                start=start,
                end=end,
                known_run_ids=indexed_run_ids,
            )
        except Exception as exc:
            logger.warning(
                "session discovery failed | agent={} error={}", agent_name, str(exc)
            )
            continue

        for session in sessions:
            if session.run_id in indexed_run_ids:
                continue

            summaries_json = json.dumps(session.summaries, ensure_ascii=True)
            summary_text = "\n".join(item for item in session.summaries if item)
            content = summary_text
            if not content:
                content = f"run:{session.run_id} agent:{session.agent_type}"

            indexed = index_session_for_fts(
                run_id=session.run_id,
                agent_type=session.agent_type,
                content=content,
                repo_name=session.repo_name,
                start_time=session.start_time,
                status=session.status,
                duration_ms=session.duration_ms,
                message_count=session.message_count,
                tool_call_count=session.tool_call_count,
                error_count=session.error_count,
                total_tokens=session.total_tokens,
                summaries=summaries_json,
                summary_text=summary_text,
                session_path=session.session_path,
            )
            if not indexed:
                continue

            indexed_run_ids.add(session.run_id)
            new_sessions.append(
                IndexedSession(
                    run_id=session.run_id,
                    agent_type=session.agent_type,
                    session_path=session.session_path,
                    start_time=session.start_time,
                )
            )

    return new_sessions if return_details else len(new_sessions)


def enqueue_session_job(
    run_id: str,
    *,
    job_type: str = JOB_TYPE_EXTRACT,
    agent_type: str | None = None,
    session_path: str | None = None,
    start_time: str | None = None,
    trigger: str | None = None,
    force: bool = False,
    max_attempts: int = 3,
) -> bool:
    """Create or reset one queue job for session extraction."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _iso_now()

    with _connect() as conn:
        existing = conn.execute(
            "SELECT status FROM session_jobs WHERE run_id = ? AND job_type = ?",
            (run_id, job_type),
        ).fetchone()

        if (
            existing
            and not force
            and str(existing.get("status") or "")
            in SESSION_JOB_ACTIVE.union({JOB_STATUS_DONE})
        ):
            return False

        if existing:
            conn.execute(
                """
                UPDATE session_jobs
                SET agent_type = ?, session_path = ?, start_time = ?, status = ?,
                    attempts = 0, trigger = ?, available_at = ?, claimed_at = NULL,
                    completed_at = NULL, heartbeat_at = NULL, error = NULL,
                    updated_at = ?, max_attempts = ?
                WHERE run_id = ? AND job_type = ?
                """,
                (
                    agent_type,
                    session_path,
                    start_time,
                    JOB_STATUS_PENDING,
                    trigger,
                    now,
                    now,
                    max(1, int(max_attempts)),
                    run_id,
                    job_type,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO session_jobs (
                    run_id, job_type, agent_type, session_path, start_time, status,
                    attempts, max_attempts, trigger, available_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    job_type,
                    agent_type,
                    session_path,
                    start_time,
                    JOB_STATUS_PENDING,
                    max(1, int(max_attempts)),
                    trigger,
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
    return True


def claim_session_jobs(
    *,
    limit: int = 20,
    run_ids: list[str] | None = None,
    job_type: str = JOB_TYPE_EXTRACT,
    timeout_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Claim available jobs, recycle stale running jobs, and mark claimed rows."""
    _ensure_sessions_db_initialized()
    limit = max(1, int(limit))
    now = _utc_now()
    now_iso = now.isoformat()
    timeout_cutoff = (
        now - timedelta(seconds=max(30, int(timeout_seconds)))
    ).isoformat()

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        stale_rows = conn.execute(
            """
            SELECT id, attempts, max_attempts
            FROM session_jobs
            WHERE status = ?
              AND COALESCE(heartbeat_at, claimed_at) IS NOT NULL
              AND COALESCE(heartbeat_at, claimed_at) < ?
            """,
            (JOB_STATUS_RUNNING, timeout_cutoff),
        ).fetchall()
        for stale in stale_rows:
            attempts = int(stale.get("attempts") or 0)
            max_attempts = int(stale.get("max_attempts") or 3)
            new_status = (
                JOB_STATUS_DEAD_LETTER
                if attempts >= max_attempts
                else JOB_STATUS_PENDING
            )
            conn.execute(
                """
                UPDATE session_jobs
                SET status = ?, available_at = ?, claimed_at = NULL, heartbeat_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_status, now_iso, now_iso, int(stale.get("id") or 0)),
            )

        where_parts = ["status IN (?, ?)", "job_type = ?", "available_at <= ?"]
        params: list[Any] = [JOB_STATUS_PENDING, JOB_STATUS_FAILED, job_type, now_iso]
        if run_ids:
            placeholders = ",".join("?" for _ in run_ids)
            where_parts.append(f"run_id IN ({placeholders})")
            params.extend(run_ids)

        rows = conn.execute(
            f"""
            SELECT *
            FROM session_jobs
            WHERE {" AND ".join(where_parts)}
            ORDER BY start_time DESC, available_at ASC, id ASC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()

        claimed: list[dict[str, Any]] = []
        for row in rows:
            job_id = int(row.get("id") or 0)
            attempts = int(row.get("attempts") or 0) + 1
            conn.execute(
                """
                UPDATE session_jobs
                SET status = ?, attempts = ?, claimed_at = ?, heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (JOB_STATUS_RUNNING, attempts, now_iso, now_iso, now_iso, job_id),
            )
            row["attempts"] = attempts
            row["status"] = JOB_STATUS_RUNNING
            row["claimed_at"] = now_iso
            row["heartbeat_at"] = now_iso
            row["updated_at"] = now_iso
            claimed.append(row)

        conn.commit()
    return claimed


def heartbeat_session_job(run_id: str, *, job_type: str = JOB_TYPE_EXTRACT) -> bool:
    """Update heartbeat timestamp for one running queue job."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _iso_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE session_jobs
            SET heartbeat_at = ?, updated_at = ?
            WHERE run_id = ? AND job_type = ? AND status = ?
            """,
            (now, now, run_id, job_type, JOB_STATUS_RUNNING),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def complete_session_job(run_id: str, *, job_type: str = JOB_TYPE_EXTRACT) -> bool:
    """Mark one queue job as completed."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _iso_now()
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE session_jobs
            SET status = ?, completed_at = ?, heartbeat_at = ?, updated_at = ?
            WHERE run_id = ? AND job_type = ?
            """,
            (JOB_STATUS_DONE, now, now, now, run_id, job_type),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def fail_session_job(
    run_id: str,
    *,
    error: str,
    retry_backoff_seconds: int = 60,
    job_type: str = JOB_TYPE_EXTRACT,
) -> bool:
    """Record job failure and schedule retry or dead-letter transition."""
    if not run_id:
        return False
    _ensure_sessions_db_initialized()
    now = _utc_now()
    now_iso = now.isoformat()

    with _connect() as conn:
        row = conn.execute(
            "SELECT attempts, max_attempts FROM session_jobs WHERE run_id = ? AND job_type = ?",
            (run_id, job_type),
        ).fetchone()
        if not row:
            return False

        attempts = int(row.get("attempts") or 0)
        max_attempts = int(row.get("max_attempts") or 3)
        exhausted = attempts >= max_attempts
        status = JOB_STATUS_DEAD_LETTER if exhausted else JOB_STATUS_FAILED
        available_at = (
            now
            if exhausted
            else now + timedelta(seconds=max(1, int(retry_backoff_seconds)))
        )

        cursor = conn.execute(
            """
            UPDATE session_jobs
            SET status = ?, available_at = ?, completed_at = ?, heartbeat_at = ?,
                updated_at = ?, error = ?
            WHERE run_id = ? AND job_type = ?
            """,
            (
                status,
                available_at.isoformat(),
                now_iso if exhausted else None,
                now_iso,
                now_iso,
                error,
                run_id,
                job_type,
            ),
        )
        conn.commit()
    return int(cursor.rowcount or 0) > 0


def list_session_jobs(
    *,
    limit: int = 100,
    status: str | None = None,
    job_type: str | None = None,
) -> list[dict[str, Any]]:
    """List queue jobs with optional status/job-type filters."""
    _ensure_sessions_db_initialized()
    limit = max(1, int(limit))
    where: list[str] = []
    params: list[Any] = []

    if status:
        where.append("status = ?")
        params.append(status)
    if job_type:
        where.append("job_type = ?")
        params.append(job_type)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM session_jobs
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
    return rows


def count_session_jobs_by_status() -> dict[str, int]:
    """Return queue counts keyed by status with zero-filled defaults."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(1) AS total FROM session_jobs GROUP BY status"
        ).fetchall()
    counts = {
        str(row.get("status") or "unknown"): int(row.get("total") or 0) for row in rows
    }
    for status in (
        JOB_STATUS_PENDING,
        JOB_STATUS_RUNNING,
        JOB_STATUS_DONE,
        JOB_STATUS_FAILED,
        JOB_STATUS_DEAD_LETTER,
    ):
        counts.setdefault(status, 0)
    return counts


def record_service_run(
    *,
    job_type: str,
    status: str,
    started_at: str,
    completed_at: str | None,
    trigger: str | None,
    details: dict[str, Any] | None,
) -> int:
    """Insert one service-run audit row and return inserted id."""
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO service_runs (job_type, status, started_at, completed_at, trigger, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_type,
                status,
                started_at,
                completed_at,
                trigger,
                json.dumps(details or {}, ensure_ascii=True),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid or 0)


def latest_service_run(job_type: str) -> dict[str, Any] | None:
    """Return most recent recorded service run for the requested job type."""
    if not job_type:
        return None
    _ensure_sessions_db_initialized()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, job_type, status, started_at, completed_at, trigger, details_json
            FROM service_runs
            WHERE job_type = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (job_type,),
        ).fetchone()
    if not row:
        return None
    details_raw = row.get("details_json")
    try:
        details = json.loads(details_raw) if details_raw else {}
    except (json.JSONDecodeError, TypeError):
        details = {}
    return {
        "id": row.get("id"),
        "job_type": row.get("job_type"),
        "status": row.get("status"),
        "started_at": row.get("started_at"),
        "completed_at": row.get("completed_at"),
        "trigger": row.get("trigger"),
        "details": details,
    }


if __name__ == "__main__":
    prev_cfg = os.getenv("LERIM_CONFIG")
    try:
        with tempfile.TemporaryDirectory(prefix="lerim-catalog-selftest-") as tmp:
            cfg_path = Path(tmp) / "test_config.toml"
            cfg_path.write_text(
                f'[data]\ndir = "{tmp}"\n\n[memory]\nscope = "global_only"\n',
                encoding="utf-8",
            )
            os.environ["LERIM_CONFIG"] = str(cfg_path)
            reload_config()
            init_sessions_db()
            run_id = "selftest-run"
            queued = enqueue_session_job(run_id, force=True)
            claimed = claim_session_jobs(limit=1, run_ids=[run_id])
            assert queued
            assert claimed and str(claimed[0].get("run_id")) == run_id
            assert heartbeat_session_job(run_id)
            assert complete_session_job(run_id)
            counts = count_session_jobs_by_status()
            assert counts.get(JOB_STATUS_DONE, 0) >= 1
    finally:
        if prev_cfg is None:
            os.environ.pop("LERIM_CONFIG", None)
        else:
            os.environ["LERIM_CONFIG"] = prev_cfg
        reload_config()
