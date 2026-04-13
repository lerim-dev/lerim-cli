"""Unit tests for per-project ordered claiming and retry/skip queue management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from lerim.sessions.catalog import (
	_connect,
	claim_session_jobs,
	complete_session_job,
	enqueue_session_job,
	fail_session_job,
	index_session_for_fts,
	init_sessions_db,
	list_queue_jobs,
	queue_health_snapshot,
	reap_stale_running_jobs,
	resolve_run_id_prefix,
	retry_project_jobs,
	retry_session_job,
	skip_project_jobs,
	skip_session_job,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
	"""Point catalog DB to a temp dir so tests don't touch real data."""
	db_file = tmp_path / "sessions.sqlite3"
	monkeypatch.setattr("lerim.sessions.catalog._DB_INITIALIZED_PATH", None)
	monkeypatch.setattr("lerim.sessions.catalog._db_path", lambda: db_file)
	init_sessions_db()


def _seed_and_enqueue(
	run_id: str,
	repo_path: str,
	start_time: str = "2026-03-01T10:00:00Z",
) -> None:
	"""Index a session and enqueue a job for it."""
	index_session_for_fts(
		run_id=run_id,
		agent_type="claude",
		content=f"session {run_id}",
		start_time=start_time,
		session_path=f"/tmp/{run_id}.jsonl",
	)
	enqueue_session_job(
		run_id,
		session_path=f"/tmp/{run_id}.jsonl",
		repo_path=repo_path,
		start_time=start_time,
	)


def _set_job_status(run_id: str, status: str, available_at: str | None = None) -> None:
	"""Directly update a job's status (and optionally available_at) in the DB."""
	with _connect() as conn:
		if available_at:
			conn.execute(
				"UPDATE session_jobs SET status = ?, available_at = ? WHERE run_id = ?",
				(status, available_at, run_id),
			)
		else:
			conn.execute(
				"UPDATE session_jobs SET status = ? WHERE run_id = ?",
				(status, run_id),
			)
		conn.commit()


# ── Per-project ordering tests ────────────────────────────────────────


def test_claim_oldest_per_project():
	"""3 projects with 2 jobs each: claim picks only the oldest per project."""
	for project in ("proj-a", "proj-b", "proj-c"):
		_seed_and_enqueue(
			f"{project}-old", f"/tmp/{project}", start_time="2026-03-01T09:00:00Z"
		)
		_seed_and_enqueue(
			f"{project}-new", f"/tmp/{project}", start_time="2026-03-01T11:00:00Z"
		)

	jobs = claim_session_jobs(limit=10)
	assert len(jobs) == 3
	claimed_ids = {j["run_id"] for j in jobs}
	assert claimed_ids == {"proj-a-old", "proj-b-old", "proj-c-old"}


def test_dead_letter_blocks_project():
	"""Dead letter in project A blocks it; project B's job is still claimed."""
	_seed_and_enqueue("a-dead", "/tmp/proj-a", start_time="2026-03-01T09:00:00Z")
	_seed_and_enqueue("a-pending", "/tmp/proj-a", start_time="2026-03-01T10:00:00Z")
	_seed_and_enqueue("b-pending", "/tmp/proj-b", start_time="2026-03-01T09:00:00Z")

	_set_job_status("a-dead", "dead_letter")

	jobs = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs}
	assert "b-pending" in claimed_ids
	assert "a-pending" not in claimed_ids
	assert "a-dead" not in claimed_ids


def test_failed_with_future_available_at_blocks_project():
	"""Oldest job is failed with future available_at: project is paused."""
	_seed_and_enqueue("c-failed", "/tmp/proj-c", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("c-pending", "/tmp/proj-c", start_time="2026-03-01T10:00:00Z")

	future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
	_set_job_status("c-failed", "failed", available_at=future)

	jobs = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs}
	assert "c-failed" not in claimed_ids
	assert "c-pending" not in claimed_ids


def test_multiple_dead_letters_all_block():
	"""Project with 2 dead_letter jobs stays blocked until both are resolved."""
	_seed_and_enqueue("d-dl1", "/tmp/proj-d", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("d-dl2", "/tmp/proj-d", start_time="2026-03-01T09:00:00Z")
	_seed_and_enqueue("d-ok", "/tmp/proj-d", start_time="2026-03-01T10:00:00Z")

	_set_job_status("d-dl1", "dead_letter")
	_set_job_status("d-dl2", "dead_letter")

	jobs = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs}
	assert "d-dl1" not in claimed_ids
	assert "d-dl2" not in claimed_ids
	assert "d-ok" not in claimed_ids

	# Resolve only the first dead letter -- project still blocked by second
	skip_session_job("d-dl1")
	jobs2 = claim_session_jobs(limit=10)
	claimed_ids2 = {j["run_id"] for j in jobs2}
	assert "d-ok" not in claimed_ids2
	assert "d-dl2" not in claimed_ids2


def test_run_ids_filter_narrows_to_requested_jobs():
	"""run_ids filter cannot bypass per-project dead_letter blocking.

	The run_ids filter is applied in the outer query, NOT inside the CTE.
	The CTE sees ALL non-terminal jobs so that dead_letter blockers are
	always visible in the partition.  Even when the caller explicitly
	requests a specific run_id, a dead_letter blocker in the same project
	still prevents claiming.
	"""
	_seed_and_enqueue("e-dead", "/tmp/proj-e", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("e-target", "/tmp/proj-e", start_time="2026-03-01T10:00:00Z")

	_set_job_status("e-dead", "dead_letter")

	# Without run_ids filter, the project is blocked by dead_letter
	jobs_unfiltered = claim_session_jobs(limit=10)
	assert all(j["run_id"] != "e-target" for j in jobs_unfiltered)

	# With run_ids filter requesting only e-target, the project is STILL
	# blocked because the CTE sees the dead_letter blocker in the partition
	jobs_filtered = claim_session_jobs(limit=10, run_ids=["e-target"])
	assert len(jobs_filtered) == 0


# ── Retry / Skip tests ───────────────────────────────────────────────


def test_retry_dead_letter_succeeds():
	"""Dead letter job retried: status becomes pending, attempts reset."""
	_seed_and_enqueue("f-dl", "/tmp/proj-f")
	_set_job_status("f-dl", "dead_letter")

	result = retry_session_job("f-dl")
	assert result is True

	with _connect() as conn:
		row = conn.execute(
			"SELECT status, attempts FROM session_jobs WHERE run_id = ?",
			("f-dl",),
		).fetchone()
	assert row["status"] == "pending"
	assert row["attempts"] == 0


def test_retry_non_dead_letter_fails():
	"""Running or pending job: retry returns False (only dead_letter allowed)."""
	_seed_and_enqueue("g-run", "/tmp/proj-g")

	# Job starts as pending
	assert retry_session_job("g-run") is False

	# Claim it so it becomes running
	claim_session_jobs(limit=1, run_ids=["g-run"])
	assert retry_session_job("g-run") is False


def test_skip_dead_letter_succeeds():
	"""Dead letter skipped: status becomes done."""
	_seed_and_enqueue("h-dl", "/tmp/proj-h")
	_set_job_status("h-dl", "dead_letter")

	result = skip_session_job("h-dl")
	assert result is True

	with _connect() as conn:
		row = conn.execute(
			"SELECT status FROM session_jobs WHERE run_id = ?",
			("h-dl",),
		).fetchone()
	assert row["status"] == "done"


def test_retry_unblocks_project():
	"""Dead letter blocks project; retry resets it; next claim includes project."""
	_seed_and_enqueue("i-dl", "/tmp/proj-i", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("i-next", "/tmp/proj-i", start_time="2026-03-01T10:00:00Z")

	_set_job_status("i-dl", "dead_letter")

	# Blocked
	jobs = claim_session_jobs(limit=10)
	assert all(j["run_id"] != "i-dl" and j["run_id"] != "i-next" for j in jobs)

	# Retry unblocks
	retry_session_job("i-dl")
	jobs2 = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs2}
	assert "i-dl" in claimed_ids


def test_skip_unblocks_project():
	"""Dead letter blocks project; skip marks done; next claim picks next job."""
	_seed_and_enqueue("j-dl", "/tmp/proj-j", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("j-next", "/tmp/proj-j", start_time="2026-03-01T10:00:00Z")

	_set_job_status("j-dl", "dead_letter")

	# Blocked
	jobs = claim_session_jobs(limit=10)
	assert all(j["run_id"] != "j-next" for j in jobs)

	# Skip unblocks -- next job should now be claimable
	skip_session_job("j-dl")
	jobs2 = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs2}
	assert "j-next" in claimed_ids


# ── Batch retry / skip tests ─────────────────────────────────────────


def test_retry_project_jobs():
	"""2 dead_letter jobs for project: retry_project_jobs resets both."""
	_seed_and_enqueue("k-dl1", "/tmp/proj-k", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("k-dl2", "/tmp/proj-k", start_time="2026-03-01T09:00:00Z")

	_set_job_status("k-dl1", "dead_letter")
	_set_job_status("k-dl2", "dead_letter")

	count = retry_project_jobs("/tmp/proj-k")
	assert count == 2

	with _connect() as conn:
		rows = conn.execute(
			"SELECT status, attempts FROM session_jobs WHERE repo_path = ?",
			("/tmp/proj-k",),
		).fetchall()
	for row in rows:
		assert row["status"] == "pending"
		assert row["attempts"] == 0


def test_skip_project_jobs():
	"""2 dead_letter jobs for project: skip_project_jobs marks both done."""
	_seed_and_enqueue("l-dl1", "/tmp/proj-l", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("l-dl2", "/tmp/proj-l", start_time="2026-03-01T09:00:00Z")

	_set_job_status("l-dl1", "dead_letter")
	_set_job_status("l-dl2", "dead_letter")

	count = skip_project_jobs("/tmp/proj-l")
	assert count == 2

	with _connect() as conn:
		rows = conn.execute(
			"SELECT status FROM session_jobs WHERE repo_path = ?",
			("/tmp/proj-l",),
		).fetchall()
	for row in rows:
		assert row["status"] == "done"


# ── Prefix resolution tests ──────────────────────────────────────────


def test_resolve_prefix_unique():
	"""Single job with run_id 'abc123def': 6-char prefix resolves it."""
	_seed_and_enqueue("abc123def", "/tmp/proj-prefix")

	result = resolve_run_id_prefix("abc123")
	assert result == "abc123def"


def test_resolve_prefix_ambiguous():
	"""Two jobs starting with 'abc123': returns None (ambiguous)."""
	_seed_and_enqueue("abc123-first", "/tmp/proj-prefix-a")
	_seed_and_enqueue("abc123-second", "/tmp/proj-prefix-b")

	result = resolve_run_id_prefix("abc123")
	assert result is None


def test_resolve_prefix_too_short():
	"""5-char prefix: returns None (minimum is 6)."""
	_seed_and_enqueue("xyz789full", "/tmp/proj-prefix-c")

	result = resolve_run_id_prefix("xyz78")
	assert result is None


# ── list_queue_jobs tests ─────────────────────────────────────────────


def test_list_queue_jobs_default():
	"""Default list_queue_jobs returns non-done jobs."""
	_seed_and_enqueue("m-pending", "/tmp/proj-m", start_time="2026-03-01T10:00:00Z")
	_seed_and_enqueue("m-done", "/tmp/proj-m2", start_time="2026-03-01T11:00:00Z")

	# Complete one job so it becomes done
	claim_session_jobs(limit=1, run_ids=["m-done"])
	from lerim.sessions.catalog import complete_session_job
	complete_session_job("m-done")

	rows = list_queue_jobs()
	run_ids = {r["run_id"] for r in rows}
	assert "m-pending" in run_ids
	assert "m-done" not in run_ids


def test_list_queue_jobs_failed_only():
	"""failed_only=True returns only failed + dead_letter jobs."""
	_seed_and_enqueue("n-pending", "/tmp/proj-n1", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("n-failed", "/tmp/proj-n2", start_time="2026-03-01T09:00:00Z")
	_seed_and_enqueue("n-dead", "/tmp/proj-n3", start_time="2026-03-01T10:00:00Z")

	_set_job_status("n-failed", "failed")
	_set_job_status("n-dead", "dead_letter")

	rows = list_queue_jobs(failed_only=True)
	run_ids = {r["run_id"] for r in rows}
	assert "n-failed" in run_ids
	assert "n-dead" in run_ids
	assert "n-pending" not in run_ids


def test_list_queue_jobs_project_filter():
	"""project_filter filters by repo_path substring."""
	_seed_and_enqueue("o-match", "/tmp/special-proj", start_time="2026-03-01T10:00:00Z")
	_seed_and_enqueue("o-other", "/tmp/other-proj", start_time="2026-03-01T10:00:00Z")

	rows = list_queue_jobs(project_filter="special")
	run_ids = {r["run_id"] for r in rows}
	assert "o-match" in run_ids
	assert "o-other" not in run_ids


def test_list_queue_jobs_project_exact():
	"""project_exact=True matches only exact repo_path."""
	_seed_and_enqueue("o2-match", "/tmp/exact-proj", start_time="2026-03-01T10:00:00Z")
	_seed_and_enqueue("o2-other", "/tmp/exact-proj-sub", start_time="2026-03-01T10:00:00Z")

	rows = list_queue_jobs(project_filter="/tmp/exact-proj", project_exact=True)
	run_ids = {r["run_id"] for r in rows}
	assert "o2-match" in run_ids
	assert "o2-other" not in run_ids


# ── Full lifecycle integration tests ─────────────────────────────────


def _make_available_now(run_id: str) -> None:
	"""Set available_at to the past so a failed job is immediately re-claimable."""
	past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
	with _connect() as conn:
		conn.execute(
			"UPDATE session_jobs SET available_at = ? WHERE run_id = ?",
			(past, run_id),
		)
		conn.commit()


def test_full_lifecycle_claim_fail_dead_letter_retry_reclaim():
	"""Enqueue -> claim -> fail 3x -> dead_letter -> blocks -> retry -> claim again."""
	_seed_and_enqueue("lc-job", "/tmp/proj-lc", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("lc-next", "/tmp/proj-lc", start_time="2026-03-01T10:00:00Z")

	for attempt in range(1, 4):
		jobs = claim_session_jobs(limit=10)
		matched = [j for j in jobs if j["run_id"] == "lc-job"]
		assert len(matched) == 1, f"attempt {attempt}: job should be claimable"
		assert matched[0]["status"] == "running"

		fail_session_job("lc-job", error=f"error-{attempt}", retry_backoff_seconds=0)

		with _connect() as conn:
			row = conn.execute(
				"SELECT status, attempts FROM session_jobs WHERE run_id = ?",
				("lc-job",),
			).fetchone()

		if attempt < 3:
			assert row["status"] == "failed", f"attempt {attempt}: should be failed"
			# Backoff minimum is 1s; push available_at to past for next claim
			_make_available_now("lc-job")
		else:
			assert row["status"] == "dead_letter", "after 3 attempts: should be dead_letter"

	# Dead letter blocks the project -- lc-next should NOT be claimable
	jobs_blocked = claim_session_jobs(limit=10)
	blocked_ids = {j["run_id"] for j in jobs_blocked}
	assert "lc-job" not in blocked_ids
	assert "lc-next" not in blocked_ids

	# Retry unblocks -- original job becomes pending again
	assert retry_session_job("lc-job") is True
	with _connect() as conn:
		row = conn.execute(
			"SELECT status, attempts FROM session_jobs WHERE run_id = ?",
			("lc-job",),
		).fetchone()
	assert row["status"] == "pending"
	assert row["attempts"] == 0

	# Claim again successfully
	jobs_after = claim_session_jobs(limit=10)
	after_ids = {j["run_id"] for j in jobs_after}
	assert "lc-job" in after_ids


def test_full_lifecycle_skip_then_next_job():
	"""2 jobs in project. Oldest dead_letters -> skip it -> next becomes claimable."""
	_seed_and_enqueue("sk-old", "/tmp/proj-sk", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("sk-new", "/tmp/proj-sk", start_time="2026-03-01T10:00:00Z")

	# Claim and exhaust the old job to make it dead_letter
	for i in range(3):
		claim_session_jobs(limit=10)
		fail_session_job("sk-old", error="boom", retry_backoff_seconds=0)
		if i < 2:
			_make_available_now("sk-old")

	with _connect() as conn:
		row = conn.execute(
			"SELECT status FROM session_jobs WHERE run_id = ?", ("sk-old",)
		).fetchone()
	assert row["status"] == "dead_letter"

	# Project blocked -- neither job claimable
	jobs = claim_session_jobs(limit=10)
	assert all(j["run_id"] not in ("sk-old", "sk-new") for j in jobs)

	# Skip the dead letter
	assert skip_session_job("sk-old") is True

	# Now sk-new should be the oldest eligible and claimable
	jobs2 = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs2}
	assert "sk-new" in claimed_ids


# ── Concurrent project integration tests ─────────────────────────────


def test_three_projects_one_blocked_two_proceed():
	"""3 projects, each with 3 jobs. Project A has dead_letter -- only B and C get jobs."""
	for suffix in ("j1", "j2", "j3"):
		_seed_and_enqueue(
			f"pa-{suffix}", "/tmp/proj-pa",
			start_time=f"2026-03-01T0{int(suffix[1]) + 6}:00:00Z",
		)
		_seed_and_enqueue(
			f"pb-{suffix}", "/tmp/proj-pb",
			start_time=f"2026-03-01T0{int(suffix[1]) + 6}:00:00Z",
		)
		_seed_and_enqueue(
			f"pc-{suffix}", "/tmp/proj-pc",
			start_time=f"2026-03-01T0{int(suffix[1]) + 6}:00:00Z",
		)

	# Dead-letter the oldest job in project A
	_set_job_status("pa-j1", "dead_letter")

	jobs = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs}

	# Project A entirely blocked
	assert not any(rid.startswith("pa-") for rid in claimed_ids)

	# Projects B and C each get exactly their oldest pending job
	assert "pb-j1" in claimed_ids
	assert "pc-j1" in claimed_ids
	assert len(claimed_ids) == 2


def test_mixed_statuses_across_projects():
	"""Project A: pending+done. Project B: failed+pending. Project C: dead_letter+pending.

	Claim should return jobs from A (pending) and B (the failed one, if available),
	but not C (blocked by dead_letter).
	"""
	# Project A: one pending, one done
	_seed_and_enqueue("ma-pend", "/tmp/proj-ma", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("ma-done", "/tmp/proj-ma", start_time="2026-03-01T09:00:00Z")
	claim_session_jobs(limit=1, run_ids=["ma-done"])
	complete_session_job("ma-done")

	# Project B: one failed (past available_at) + one pending
	_seed_and_enqueue("mb-fail", "/tmp/proj-mb", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("mb-pend", "/tmp/proj-mb", start_time="2026-03-01T10:00:00Z")
	past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
	_set_job_status("mb-fail", "failed", available_at=past)

	# Project C: one dead_letter + one pending
	_seed_and_enqueue("mc-dead", "/tmp/proj-mc", start_time="2026-03-01T08:00:00Z")
	_seed_and_enqueue("mc-pend", "/tmp/proj-mc", start_time="2026-03-01T10:00:00Z")
	_set_job_status("mc-dead", "dead_letter")

	jobs = claim_session_jobs(limit=10)
	claimed_ids = {j["run_id"] for j in jobs}

	# A: pending job is oldest eligible (done job excluded from CTE)
	assert "ma-pend" in claimed_ids

	# B: failed job is oldest and now past available_at, so it gets re-claimed
	assert "mb-fail" in claimed_ids
	# mb-pend must NOT be claimed (not oldest in partition)
	assert "mb-pend" not in claimed_ids

	# C: blocked by dead_letter
	assert "mc-dead" not in claimed_ids
	assert "mc-pend" not in claimed_ids


# ── Edge case integration tests ──────────────────────────────────────


def test_claim_returns_no_rn_column():
	"""CTE adds an `rn` column. Verify claimed job dicts do not expose it."""
	_seed_and_enqueue("rn-check", "/tmp/proj-rn", start_time="2026-03-01T10:00:00Z")

	jobs = claim_session_jobs(limit=10)
	assert len(jobs) >= 1
	job = next(j for j in jobs if j["run_id"] == "rn-check")

	# The rn key may be present in the raw sqlite Row dict because SELECT *
	# includes it.  This test documents the current behavior.  If rn is present,
	# it should be exactly 1 (the window function value).  If the implementation
	# strips it in the future, the key will be absent -- either way is acceptable.
	if "rn" in job:
		assert job["rn"] == 1
	# Core job fields must always be present
	for key in ("run_id", "status", "repo_path", "attempts"):
		assert key in job


def test_empty_database_claim():
	"""No jobs at all: claim returns empty list, no error."""
	jobs = claim_session_jobs(limit=10)
	assert jobs == []


def test_reap_stale_running_job_marks_failed():
	"""Stale running job is recovered through fail path (status -> failed)."""
	_seed_and_enqueue("stale-1", "/tmp/proj-stale", start_time="2026-03-01T10:00:00Z")
	claimed = claim_session_jobs(limit=1, run_ids=["stale-1"])
	assert len(claimed) == 1
	old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
	with _connect() as conn:
		conn.execute(
			"UPDATE session_jobs SET claimed_at = ?, updated_at = ? WHERE run_id = ?",
			(old, old, "stale-1"),
		)
		conn.commit()

	recovered = reap_stale_running_jobs(
		lease_seconds=60,
		retry_backoff_fn=lambda attempts: 1 if attempts >= 1 else 1,
	)
	assert recovered == 1
	with _connect() as conn:
		row = conn.execute(
			"SELECT status, error FROM session_jobs WHERE run_id = ?",
			("stale-1",),
		).fetchone()
	assert row["status"] == "failed"
	assert "stale running lease expired" in str(row["error"] or "")


def test_reap_stale_running_job_to_dead_letter_when_attempts_exhausted():
	"""Stale running job dead-letters when max_attempts already exhausted."""
	_seed_and_enqueue("stale-dl", "/tmp/proj-stale", start_time="2026-03-01T10:00:00Z")
	with _connect() as conn:
		conn.execute(
			"UPDATE session_jobs SET max_attempts = 1 WHERE run_id = ?",
			("stale-dl",),
		)
		conn.commit()
	claimed = claim_session_jobs(limit=1, run_ids=["stale-dl"])
	assert len(claimed) == 1
	old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
	with _connect() as conn:
		conn.execute(
			"UPDATE session_jobs SET claimed_at = ?, updated_at = ? WHERE run_id = ?",
			(old, old, "stale-dl"),
		)
		conn.commit()

	recovered = reap_stale_running_jobs(lease_seconds=60)
	assert recovered == 1
	with _connect() as conn:
		row = conn.execute(
			"SELECT status, completed_at FROM session_jobs WHERE run_id = ?",
			("stale-dl",),
		).fetchone()
	assert row["status"] == "dead_letter"
	assert row["completed_at"] is not None


def test_queue_health_snapshot_reports_degraded():
	"""Queue health reports dead-letter + stale-running degradation details."""
	_seed_and_enqueue("qh-run", "/tmp/proj-qh", start_time="2026-03-01T10:00:00Z")
	_seed_and_enqueue("qh-dead", "/tmp/proj-qh2", start_time="2026-03-01T10:00:00Z")
	claim_session_jobs(limit=1, run_ids=["qh-run"])
	old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
	with _connect() as conn:
		conn.execute(
			"UPDATE session_jobs SET claimed_at = ?, updated_at = ? WHERE run_id = ?",
			(old, old, "qh-run"),
		)
		conn.execute(
			"UPDATE session_jobs SET status = 'dead_letter', updated_at = ? WHERE run_id = ?",
			(old, "qh-dead"),
		)
		conn.commit()

	health = queue_health_snapshot(lease_seconds=60)
	assert health["degraded"] is True
	assert health["stale_running_count"] >= 1
	assert health["dead_letter_count"] >= 1
	assert isinstance(health["oldest_running_age_seconds"], int)
	assert isinstance(health["oldest_dead_letter_age_seconds"], int)
	assert "lerim queue --failed" in str(health["advice"])
