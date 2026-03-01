"""Append-only activity log at ~/.lerim/activity.log.

Records one line per sync/maintain/ask operation with timestamp, project, stats, and duration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


ACTIVITY_LOG_PATH = Path.home() / ".lerim" / "activity.log"


def log_activity(op: str, project: str, stats: str, duration_s: float) -> None:
    """Append one line to ~/.lerim/activity.log.

    Format: ``2026-03-01 14:23:05 | sync | myproject | 3 new, 1 updated | 4.2s``
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} | {op:<8} | {project} | {stats} | {duration_s:.1f}s\n"
    ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITY_LOG_PATH, "a") as f:
        f.write(line)
