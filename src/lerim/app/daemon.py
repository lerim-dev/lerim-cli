"""Sync/maintain daemon orchestration, locking, and service run reporting."""

from __future__ import annotations

import json
import os
import sqlite3
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from lerim.app.arg_utils import parse_duration_to_seconds
from lerim.config.project_scope import match_session_project
from lerim.config.settings import get_config, reload_config
from lerim.runtime.agent import LerimAgent
from lerim.sessions.catalog import (
    IndexedSession,
    claim_session_jobs,
    complete_session_job,
    enqueue_session_job,
    fail_session_job,
    fetch_session_doc,
    heartbeat_session_job,
    index_new_sessions,
    record_service_run,
)


EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PARTIAL = 3
EXIT_LOCK_BUSY = 4
WRITER_LOCK_NAME = "writer.lock"


def lock_path(name: str) -> Path:
    """Return lock file path under configured index directory."""
    return get_config().index_dir / name


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse ISO timestamp strings safely."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _retry_backoff_seconds(attempts: int) -> int:
    """Return bounded exponential retry backoff in seconds."""
    safe_attempts = max(attempts, 1)
    return min(3600, 30 * (2 ** (safe_attempts - 1)))


def _now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _empty_sync_summary() -> SyncSummary:
    """Return an empty sync summary payload."""
    return SyncSummary(
        indexed_sessions=0,
        extracted_sessions=0,
        skipped_sessions=0,
        failed_sessions=0,
        learnings_new=0,
        learnings_updated=0,
        run_ids=[],
    )


def _record_service_event(
    record_fn: Callable[..., Any],
    *,
    job_type: str,
    status: str,
    started_at: str,
    trigger: str,
    details: dict[str, Any],
) -> None:
    """Record a service run with canonical completed timestamp."""
    record_fn(
        job_type=job_type,
        status=status,
        started_at=started_at,
        completed_at=_now_iso(),
        trigger=trigger,
        details=details,
    )


@contextmanager
def _job_heartbeat(
    run_id: str, heartbeat_func: Callable[[str], bool], interval_seconds: int = 15
):
    """Background heartbeat helper for long-running job processing."""
    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        """Emit heartbeat updates until the stop event is set."""
        while not stop_heartbeat.wait(interval_seconds):
            heartbeat_func(run_id)

    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        name=f"lerim-heartbeat-{run_id[:8]}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        yield
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1.0)


def _pid_alive(pid: int | None) -> bool:
    """Return whether a PID appears alive on this host."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_stale(state: dict[str, object], stale_seconds: int) -> bool:
    """Return whether lock heartbeat state is stale."""
    heartbeat = _parse_iso(str(state.get("heartbeat_at") or ""))
    if not heartbeat:
        return True
    elapsed = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    return elapsed > max(stale_seconds, 1)


def read_json_file(path: Path) -> dict[str, object] | None:
    """Read a JSON object file; return ``None`` on failures."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def active_lock_state(path: Path, stale_seconds: int = 60) -> dict[str, object] | None:
    """Return active non-stale lock state or ``None`` when stale/missing."""
    state = read_json_file(path)
    if not state:
        return None
    pid = state.get("pid")
    if _pid_alive(pid if isinstance(pid, int) else None) and not _is_stale(
        state, stale_seconds
    ):
        return state
    return None


@dataclass
class LockBusyError(RuntimeError):
    """Raised when a service lock is currently held by another live process."""

    lock_path: Path
    state: dict[str, object] | None = None

    def __str__(self) -> str:
        """Render lock owner details for user-facing errors."""
        if self.state:
            owner = self.state.get("owner") or "unknown"
            pid = self.state.get("pid") or "unknown"
            return f"lock busy: {self.lock_path} (owner={owner}, pid={pid})"
        return f"lock busy: {self.lock_path}"


class ServiceLock:
    """Filesystem lock helper with stale lock reclamation."""

    def __init__(self, path: Path, stale_seconds: int = 60) -> None:
        """Store lock path and stale threshold for acquire/release calls."""
        self.path = path
        self.stale_seconds = stale_seconds
        self._held = False

    def acquire(self, owner: str, command: str) -> dict[str, object]:
        """Acquire lock file or raise ``LockBusyError`` if still active."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            state: dict[str, object] = {
                "pid": os.getpid(),
                "owner": owner,
                "command": command,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                "host": socket.gethostname() or "local",
            }
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(state, ensure_ascii=True, indent=2))
                    handle.write("\n")
                self._held = True
                return state
            except FileExistsError:
                active = active_lock_state(self.path, stale_seconds=self.stale_seconds)
                if active:
                    raise LockBusyError(self.path, active)
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    raise LockBusyError(self.path, read_json_file(self.path))
        raise LockBusyError(self.path, read_json_file(self.path))

    def release(self) -> None:
        """Release lock only when held by current process."""
        if not self._held:
            return
        state = read_json_file(self.path)
        if state and state.get("pid") != os.getpid():
            self._held = False
            return
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        self._held = False


@dataclass(frozen=True)
class SyncSummary:
    """Summary payload for one sync execution."""

    indexed_sessions: int
    extracted_sessions: int
    skipped_sessions: int
    failed_sessions: int
    learnings_new: int
    learnings_updated: int
    run_ids: list[str]


def resolve_window_bounds(
    *,
    window: str | None,
    since_raw: str | None,
    until_raw: str | None,
    parse_duration_to_seconds: Callable[[str], int],
) -> tuple[datetime | None, datetime]:
    """Resolve sync/maintain time window from CLI arguments."""
    now = datetime.now(timezone.utc)
    since = _parse_iso(since_raw)
    until = _parse_iso(until_raw) or now
    if since and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    if window and (since_raw or until_raw):
        raise ValueError("--window cannot be combined with --since/--until")
    if since and since > until:
        raise ValueError("--since must be before --until")
    if since:
        return since, until

    if not window:
        days = get_config().sync_window_days
        seconds = parse_duration_to_seconds(f"{days}d")
        return until - timedelta(seconds=seconds), until
    if window == "all":
        try:
            with sqlite3.connect(get_config().sessions_db_path) as conn:
                row = conn.execute(
                    "SELECT MIN(start_time) FROM session_docs WHERE start_time IS NOT NULL AND start_time != ''"
                ).fetchone()
            start_raw = row[0] if row else None
        except sqlite3.Error:
            start_raw = None
        if not start_raw:
            return None, until
        parsed = _parse_iso(str(start_raw))
        if not parsed:
            return None, until
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, until
    seconds = parse_duration_to_seconds(window)
    return until - timedelta(seconds=seconds), until


def _process_one_job(job: dict[str, Any]) -> dict[str, Any]:
    """Process a single claimed session job. Thread-safe (own agent instance)."""
    rid = str(job.get("run_id") or "")
    if not rid:
        return {"status": "skipped"}

    repo_path = str(job.get("repo_path") or "").strip()

    # Skip sessions that don't match a registered project
    if not repo_path:
        complete_session_job(rid)
        return {"status": "skipped", "reason": "no_project_match"}

    # Validate project directory still exists
    if not Path(repo_path).is_dir():
        complete_session_job(rid)
        return {"status": "skipped", "reason": "project_dir_missing"}

    # Route memories to the project's .lerim/memory/
    project_memory = str(Path(repo_path) / ".lerim" / "memory")

    attempts = max(int(job.get("attempts") or 1), 1)
    try:
        with _job_heartbeat(rid, heartbeat_session_job):
            session_path = str(job.get("session_path") or "").strip()
            if not session_path:
                doc = fetch_session_doc(rid) or {}
                session_path = str(doc.get("session_path") or "").strip()
            agent = LerimAgent(default_cwd=repo_path)
            result = agent.sync(Path(session_path), memory_root=project_memory)
    except Exception as exc:
        fail_session_job(
            rid,
            error=str(exc),
            retry_backoff_seconds=_retry_backoff_seconds(attempts),
        )
        return {"status": "failed"}
    counts = result.get("counts") or {}
    complete_session_job(rid)
    return {
        "status": "extracted",
        "learnings_new": int(counts.get("add") or 0),
        "learnings_updated": int(counts.get("update") or 0),
    }


def _process_claimed_jobs(
    claimed: list[dict[str, Any]],
    *,
    max_workers: int = 4,
) -> tuple[int, int, int, int, int]:
    """Process claimed jobs in parallel. Returns (extracted, failed, skipped, new, updated)."""
    extracted = 0
    failed = 0
    skipped = 0
    learnings_new = 0
    learnings_updated = 0
    workers = min(max(max_workers, 1), len(claimed)) if claimed else 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one_job, job): job for job in claimed}
        for future in as_completed(futures):
            result = future.result()
            if result["status"] == "extracted":
                extracted += 1
                learnings_new += result.get("learnings_new", 0)
                learnings_updated += result.get("learnings_updated", 0)
            elif result["status"] == "failed":
                failed += 1
            elif result["status"] == "skipped":
                skipped += 1
    return extracted, failed, skipped, learnings_new, learnings_updated


def run_sync_once(
    *,
    run_id: str | None,
    agent_filter: list[str] | None,
    no_extract: bool,
    force: bool,
    max_sessions: int,
    dry_run: bool,
    ignore_lock: bool,
    trigger: str,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> tuple[int, SyncSummary]:
    """Run one sync cycle: index sessions, enqueue jobs, process extraction."""
    reload_config()

    started = _now_iso()
    status = "completed"
    lock = None
    if not dry_run and not ignore_lock:
        lock = ServiceLock(lock_path(WRITER_LOCK_NAME), stale_seconds=60)
        try:
            lock.acquire("sync", "lerim sync")
        except LockBusyError as exc:
            _record_service_event(
                record_service_run,
                job_type="sync",
                status="lock_busy",
                started_at=started,
                trigger=trigger,
                details={"error": str(exc)},
            )
            return EXIT_LOCK_BUSY, _empty_sync_summary()

    try:
        config = get_config()
        target_run_ids: list[str] = []
        indexed_sessions = 0
        queued_sessions = 0
        if run_id:
            target_run_ids = [run_id]
            if not dry_run:
                session = fetch_session_doc(run_id)
                session_repo_path = (
                    str(session.get("repo_path") or "") if session else ""
                )
                match = match_session_project(
                    session_repo_path or None, config.projects
                )
                matched_path = str(match[1]) if match else None
                queued = enqueue_session_job(
                    run_id,
                    agent_type=session.get("agent_type") if session else None,
                    session_path=session.get("session_path") if session else None,
                    start_time=session.get("start_time") if session else None,
                    trigger=trigger,
                    force=True,
                    repo_path=matched_path,
                )
                queued_sessions = 1 if queued else 0
        else:
            if dry_run:
                target_run_ids = []
            else:
                indexed = index_new_sessions(
                    agents=agent_filter,
                    return_details=True,
                    start=window_start,
                    end=window_end,
                )
                details: list[IndexedSession] = (
                    indexed if isinstance(indexed, list) else []
                )
                indexed_sessions = len(details)
                for item in details:
                    match = match_session_project(
                        item.repo_path, config.projects
                    )
                    if match is None:
                        continue
                    _project_name, project_path = match
                    queued = enqueue_session_job(
                        item.run_id,
                        agent_type=item.agent_type,
                        session_path=item.session_path,
                        start_time=item.start_time,
                        trigger=trigger,
                        force=item.changed,
                        repo_path=str(project_path),
                    )
                    if queued:
                        queued_sessions += 1
                target_run_ids = [item.run_id for item in details]

        extracted = 0
        skipped = 0
        failed = 0
        learnings_new = 0
        learnings_updated = 0
        claim_limit = max(max_sessions, 1)

        if no_extract:
            skipped = len(target_run_ids)
        elif not dry_run:
            claimed = claim_session_jobs(
                limit=claim_limit,
                run_ids=[run_id] if run_id else None,
            )
            target_run_ids = [
                str(item.get("run_id") or "") for item in claimed if item.get("run_id")
            ]
            max_workers = get_config().sync_max_workers
            extracted, failed, routing_skipped, learnings_new, learnings_updated = _process_claimed_jobs(
                claimed,
                max_workers=max_workers,
            )
            skipped += routing_skipped

        summary = SyncSummary(
            indexed_sessions=indexed_sessions,
            extracted_sessions=extracted,
            skipped_sessions=skipped,
            failed_sessions=failed,
            learnings_new=learnings_new,
            learnings_updated=learnings_updated,
            run_ids=target_run_ids,
        )

        code = EXIT_OK
        if failed > 0 and extracted > 0:
            code = EXIT_PARTIAL
            status = "partial"
        elif failed > 0 and extracted == 0 and indexed_sessions == 0:
            code = EXIT_FATAL
            status = "failed"

        _record_service_event(
            record_service_run,
            job_type="sync",
            status=status
            if code == EXIT_OK
            else ("partial" if code == EXIT_PARTIAL else "failed"),
            started_at=started,
            trigger=trigger,
            details={
                "indexed_sessions": indexed_sessions,
                "queued_sessions": queued_sessions,
                "extracted_sessions": extracted,
                "skipped_sessions": skipped,
                "failed_sessions": failed,
                "learnings_new": learnings_new,
                "learnings_updated": learnings_updated,
                "run_ids": target_run_ids,
                "window_start": window_start.isoformat() if window_start else None,
                "window_end": window_end.isoformat() if window_end else None,
                "dry_run": dry_run,
            },
        )
        return code, summary
    finally:
        if lock:
            lock.release()


def run_maintain_once(
    *,
    force: bool,
    dry_run: bool,
) -> tuple[int, dict]:
    """Run one maintain cycle with lock handling and service run record."""
    reload_config()

    started = _now_iso()

    if dry_run:
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="completed",
            started_at=started,
            trigger="manual",
            details={"dry_run": True},
        )
        return EXIT_OK, {"dry_run": True}

    writer = ServiceLock(lock_path(WRITER_LOCK_NAME), stale_seconds=60)
    try:
        writer.acquire("maintain", "lerim maintain")
    except LockBusyError as exc:
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="lock_busy",
            started_at=started,
            trigger="manual",
            details={"error": str(exc)},
        )
        return EXIT_LOCK_BUSY, {"error": str(exc)}

    try:
        agent = LerimAgent(default_cwd=str(Path.cwd()))
        result = agent.maintain()
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="completed",
            started_at=started,
            trigger="manual",
            details=result,
        )
        return EXIT_OK, result
    except Exception as exc:
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="failed",
            started_at=started,
            trigger="manual",
            details={"error": str(exc)},
        )
        return EXIT_FATAL, {"error": str(exc)}
    finally:
        writer.release()


def run_daemon_once(max_sessions: int | None = None) -> dict:
    """Run one bounded daemon cycle: sync then maintain."""
    config = get_config()
    effective_max = max_sessions or config.sync_max_sessions
    window_start, window_end = resolve_window_bounds(
        window=f"{config.sync_window_days}d",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )

    sync_code, sync_summary = run_sync_once(
        run_id=None,
        agent_filter=None,
        no_extract=False,
        force=False,
        max_sessions=effective_max,
        dry_run=False,
        ignore_lock=False,
        trigger="daemon",
        window_start=window_start,
        window_end=window_end,
    )
    maintain_code, maintain_data = run_maintain_once(
        force=False,
        dry_run=False,
    )
    return {
        "sync_code": sync_code,
        "sync_summary": sync_summary.__dict__,
        "maintain_code": maintain_code,
        "maintain": maintain_data,
    }


def run_daemon_forever(poll_seconds: int | None = None) -> None:
    """Run daemon loop with independent sync and maintain intervals.

    When *poll_seconds* is given (e.g. via ``--poll-seconds``), both intervals
    are overridden uniformly so the caller gets a single predictable cadence.
    Otherwise ``sync_interval_minutes`` and ``maintain_interval_minutes`` from
    config drive each path independently.
    """
    config = get_config()

    if poll_seconds and poll_seconds > 0:
        sync_interval = poll_seconds
        maintain_interval = poll_seconds
    else:
        sync_interval = max(config.sync_interval_minutes * 60, 30)
        maintain_interval = max(config.maintain_interval_minutes * 60, 30)

    last_sync = 0.0
    last_maintain = 0.0

    from lerim.config.logging import logger

    while True:
        now = time.monotonic()

        if now - last_sync >= sync_interval:
            try:
                _run_sync_cycle()
            except Exception as exc:
                logger.warning("daemon sync error: {}", exc)
            last_sync = time.monotonic()

        if now - last_maintain >= maintain_interval:
            try:
                _run_maintain_cycle()
            except Exception as exc:
                logger.warning("daemon maintain error: {}", exc)
            last_maintain = time.monotonic()

        # Sleep until the next task is due.
        next_sync = last_sync + sync_interval
        next_maintain = last_maintain + maintain_interval
        sleep_for = max(1.0, min(next_sync, next_maintain) - time.monotonic())
        time.sleep(sleep_for)


def _run_sync_cycle() -> tuple[int, SyncSummary]:
    """Execute one sync cycle using current config window bounds."""
    config = get_config()
    window_start, window_end = resolve_window_bounds(
        window=f"{config.sync_window_days}d",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )
    return run_sync_once(
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


def _run_maintain_cycle() -> tuple[int, dict]:
    """Execute one maintain cycle."""
    return run_maintain_once(force=False, dry_run=False)


if __name__ == "__main__":
    since, until = resolve_window_bounds(
        window="1d",
        since_raw=None,
        until_raw=None,
        parse_duration_to_seconds=parse_duration_to_seconds,
    )
    assert until is not None
    assert since is None or since <= until
    assert callable(run_maintain_once)
