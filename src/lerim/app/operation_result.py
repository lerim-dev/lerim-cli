"""Structured result from sync/maintain operations.

Single source of truth for: local SQLite service_runs, cloud shipper,
and Logfire span attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


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
	learnings_new: int = 0
	learnings_updated: int = 0
	memory_actions: list[dict[str, str]] = field(default_factory=list)
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
			attrs["learnings_new"] = self.learnings_new
			attrs["learnings_updated"] = self.learnings_updated
		elif self.operation == "maintain":
			attrs["projects_count"] = len(self.projects)
		if self.cost_usd:
			attrs["cost_usd"] = self.cost_usd
		if self.error:
			attrs["error"] = self.error
		return attrs
