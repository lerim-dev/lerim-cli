"""Sync/maintain daemon orchestration, locking, and service run reporting."""

from __future__ import annotations

import json
import os
import sqlite3
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from lerim.config.project_scope import match_session_project
from lerim.config.settings import get_config, reload_config
from lerim.server.runtime import LerimRuntime
from lerim.sessions.catalog import (
    IndexedSession,
    claim_session_jobs,
    complete_session_job,
    enqueue_session_job,
    fail_session_job,
    fetch_session_doc,
    index_new_sessions,
    record_service_run,
)


ACTIVITY_LOG_PATH = Path.home() / ".lerim" / "activity.log"


def log_activity(
	op: str, project: str, stats: str, duration_s: float, cost_usd: float = 0.0
) -> None:
	"""Append one line to ~/.lerim/activity.log.

	Format: ``2026-03-01 14:23:05 | sync | myproject | 3 new, 1 updated | $0.0042 | 4.2s``
	"""
	ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
	cost_str = f"${cost_usd:.4f}"
	line = f"{ts} | {op:<8} | {project} | {stats} | {cost_str} | {duration_s:.1f}s\n"
	ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
	with open(ACTIVITY_LOG_PATH, "a") as f:
		f.write(line)


@dataclass
class OperationResult:
	"""Unified result payload for sync and maintain operations."""

	operation: str  # "sync" or "maintain"
	status: str  # "completed", "partial", "failed", "lock_busy"
	trigger: str  # "daemon", "manual", "api"

	# Sync-specific
	indexed_sessions: int = 0
	queued_sessions: int = 0
	extracted_sessions: int = 0
	skipped_sessions: int = 0
	failed_sessions: int = 0
	run_ids: list[str] = field(default_factory=list)
	window_start: str | None = None
	window_end: str | None = None

	# Maintain-specific
	projects: dict[str, Any] = field(default_factory=dict)

	# Shared
	cost_usd: float = 0.0
	error: str | None = None
	dry_run: bool = False

	def to_details_json(self) -> dict[str, Any]:
		"""Serialize for service_runs.details_json storage.

		Strips operation/status/trigger (already separate columns in service_runs)
		and None values to keep the JSON compact.
		"""
		d = asdict(self)
		return {
			k: v
			for k, v in d.items()
			if v is not None
			and v != 0
			and v != []
			and v != {}
			and v is not False
			and k not in ("operation", "status", "trigger")
		}

	def to_span_attrs(self) -> dict[str, Any]:
		"""Return flat key-value attributes for Logfire span."""
		attrs: dict[str, Any] = {
			"operation": self.operation,
			"status": self.status,
			"trigger": self.trigger,
		}
		if self.operation == "sync":
			attrs["indexed_sessions"] = self.indexed_sessions
			attrs["extracted_sessions"] = self.extracted_sessions
			attrs["failed_sessions"] = self.failed_sessions
		elif self.operation == "maintain":
			attrs["projects_count"] = len(self.projects)
		if self.cost_usd:
			attrs["cost_usd"] = self.cost_usd
		if self.error:
			attrs["error"] = self.error
		return attrs


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
    run_ids: list[str]
    cost_usd: float = 0.0


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
        session_path = str(job.get("session_path") or "").strip()
        if not session_path:
            doc = fetch_session_doc(rid) or {}
            session_path = str(doc.get("session_path") or "").strip()
        agent = LerimRuntime(default_cwd=repo_path)
        result = agent.sync(Path(session_path), memory_root=project_memory)
    except Exception as exc:
        fail_session_job(
            rid,
            error=str(exc),
            retry_backoff_seconds=_retry_backoff_seconds(attempts),
        )
        return {"status": "failed"}
    complete_session_job(rid)
    return {
        "status": "extracted",
        "cost_usd": float(result.get("cost_usd") or 0),
    }


def _process_claimed_jobs(
    claimed: list[dict[str, Any]],
) -> tuple[int, int, int, float]:
    """Process claimed jobs sequentially in chronological order.

    Jobs are already sorted oldest-first by ``claim_session_jobs``.
    Sequential processing ensures that later sessions can correctly
    update or supersede memories created by earlier ones.

    Returns (extracted, failed, skipped, cost_usd).
    """
    extracted = 0
    failed = 0
    skipped = 0
    cost_usd = 0.0
    for job in claimed:
        result = _process_one_job(job)
        if result["status"] == "extracted":
            extracted += 1
            cost_usd += result.get("cost_usd", 0.0)
        elif result["status"] == "failed":
            failed += 1
        elif result["status"] == "skipped":
            skipped += 1
    return extracted, failed, skipped, cost_usd


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
    t0 = time.monotonic()
    reload_config()

    started = _now_iso()
    status = "completed"
    lock = None
    if not dry_run and not ignore_lock:
        lock = ServiceLock(lock_path(WRITER_LOCK_NAME), stale_seconds=60)
        try:
            lock.acquire("sync", "lerim sync")
        except LockBusyError as exc:
            op_result = OperationResult(
                operation="sync",
                status="lock_busy",
                trigger=trigger,
                error=str(exc),
            )
            _record_service_event(
                record_service_run,
                job_type="sync",
                status="lock_busy",
                started_at=started,
                trigger=trigger,
                details=op_result.to_details_json(),
            )
            return EXIT_LOCK_BUSY, _empty_sync_summary()

    try:
        record_service_run(
            job_type="sync",
            status="started",
            started_at=started,
            completed_at=None,
            trigger=trigger,
            details=None,
        )

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
                    match = match_session_project(item.repo_path, config.projects)
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
        cost_usd = 0.0
        projects: set[str] = set()
        claim_limit = max(max_sessions, 1)

        if no_extract:
            skipped = len(target_run_ids)
        elif not dry_run:
            # Process up to max_sessions by claiming in a loop.
            # Each claim returns 1 per project (chronological ordering).
            # After processing, claim again to get the next session.
            total_processed = 0
            while total_processed < claim_limit:
                claimed = claim_session_jobs(
                    limit=claim_limit - total_processed,
                    run_ids=[run_id] if run_id else None,
                )
                if not claimed:
                    break  # no more pending jobs
                for job in claimed:
                    rp = str(job.get("repo_path") or "").strip()
                    if rp:
                        projects.add(Path(rp).name)
                target_run_ids.extend(
                    str(item.get("run_id") or "") for item in claimed if item.get("run_id")
                )
                (
                    batch_extracted,
                    batch_failed,
                    batch_skipped,
                    batch_cost,
                ) = _process_claimed_jobs(claimed)
                extracted += batch_extracted
                failed += batch_failed
                skipped += batch_skipped
                cost_usd += batch_cost
                total_processed += len(claimed)

        summary = SyncSummary(
            indexed_sessions=indexed_sessions,
            extracted_sessions=extracted,
            skipped_sessions=skipped,
            failed_sessions=failed,
            run_ids=target_run_ids,
            cost_usd=cost_usd,
        )

        code = EXIT_OK
        if failed > 0 and extracted > 0:
            code = EXIT_PARTIAL
            status = "partial"
        elif failed > 0 and extracted == 0 and indexed_sessions == 0:
            code = EXIT_FATAL
            status = "failed"

        op_result = OperationResult(
            operation="sync",
            status=status
            if code == EXIT_OK
            else ("partial" if code == EXIT_PARTIAL else "failed"),
            trigger=trigger,
            indexed_sessions=indexed_sessions,
            queued_sessions=queued_sessions,
            extracted_sessions=extracted,
            skipped_sessions=skipped,
            failed_sessions=failed,
            run_ids=target_run_ids,
            window_start=window_start.isoformat() if window_start else None,
            window_end=window_end.isoformat() if window_end else None,
            dry_run=dry_run,
            cost_usd=cost_usd,
        )
        _record_service_event(
            record_service_run,
            job_type="sync",
            status=op_result.status,
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        if not dry_run and extracted:
            log_activity(
                "sync",
                ", ".join(sorted(projects)) or "global",
                f"{extracted} sessions",
                time.monotonic() - t0,
                cost_usd=cost_usd,
            )
        return code, summary
    except Exception as exc:
        op_result = OperationResult(
            operation="sync",
            status="failed",
            trigger=trigger,
            error=str(exc),
        )
        _record_service_event(
            record_service_run,
            job_type="sync",
            status="failed",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_FATAL, _empty_sync_summary()
    finally:
        if lock:
            lock.release()


def run_maintain_once(
    *,
    force: bool,
    dry_run: bool,
    trigger: str = "manual",
) -> tuple[int, dict]:
    """Run one maintain cycle with lock handling and service run record."""
    t0 = time.monotonic()
    reload_config()

    started = _now_iso()

    if dry_run:
        op_result = OperationResult(
            operation="maintain",
            status="completed",
            trigger=trigger,
            dry_run=True,
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="completed",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_OK, {"dry_run": True}

    writer = ServiceLock(lock_path(WRITER_LOCK_NAME), stale_seconds=60)
    try:
        writer.acquire("maintain", "lerim maintain")
    except LockBusyError as exc:
        op_result = OperationResult(
            operation="maintain",
            status="lock_busy",
            trigger=trigger,
            error=str(exc),
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="lock_busy",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_LOCK_BUSY, {"error": str(exc)}

    try:
        config = get_config()
        projects = config.projects or {}
        if not projects:
            # No registered projects — maintain CWD-based fallback.
            projects = {"global": str(Path.cwd())}

        results: dict[str, dict] = {}
        failed_projects: list[str] = []
        for project_name, project_path_str in projects.items():
            project_path = Path(project_path_str).expanduser().resolve()
            if not project_path.is_dir():
                continue
            project_memory = str(project_path / ".lerim" / "memory")
            try:
                agent = LerimRuntime(default_cwd=str(project_path))
                result = agent.maintain(memory_root=project_memory)
                results[project_name] = result
                # Check for memory index
                memory_index_path = Path(project_memory) / "index.md"
                if memory_index_path.exists():
                    result["memory_index_exists"] = True
                maintain_cost = float(result.get("cost_usd") or 0)
                if maintain_cost:
                    log_activity(
                        "maintain",
                        project_name,
                        "maintenance completed",
                        time.monotonic() - t0,
                        cost_usd=maintain_cost,
                    )
            except Exception as exc:
                failed_projects.append(project_name)
                results[project_name] = {"error": str(exc)}

        status = (
            "failed"
            if failed_projects and not (set(projects) - set(failed_projects))
            else ("partial" if failed_projects else "completed")
        )
        op_result = OperationResult(
            operation="maintain",
            status=status,
            trigger=trigger,
            projects=results,
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status=status,
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        code = (
            EXIT_FATAL
            if status == "failed"
            else (EXIT_PARTIAL if status == "partial" else EXIT_OK)
        )
        return code, op_result.to_details_json()
    except Exception as exc:
        op_result = OperationResult(
            operation="maintain",
            status="failed",
            trigger=trigger,
            error=str(exc),
        )
        _record_service_event(
            record_service_run,
            job_type="maintain",
            status="failed",
            started_at=started,
            trigger=trigger,
            details=op_result.to_details_json(),
        )
        return EXIT_FATAL, {"error": str(exc)}
    finally:
        writer.release()


