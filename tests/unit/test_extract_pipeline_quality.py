"""Quality-oriented unit tests for extraction post-processing."""

from __future__ import annotations

from lerim.memory.extract_pipeline import (
	ConsolidateCandidatesSignature,
	MemoryExtractionPipeline,
	QualityGateSignature,
	_filter_candidates,
	_to_dicts,
)
from lerim.memory.schemas import MemoryCandidate


def test_filter_candidates_drops_learning_without_kind():
	"""Learning candidates without a valid kind should be dropped before sync."""
	candidates = [
		{
			"primitive": "learning",
			"title": "Queue retries need limits",
			"body": "Queue retries need limits. WHY: unbounded retries cause noisy failures. HOW TO APPLY: cap attempts at three.",
			"confidence": 0.8,
			"durability": "project",
			"tags": ["queue"],
		},
		{
			"primitive": "learning",
			"kind": "pitfall",
			"title": "Queue retries need limits",
			"body": "Queue retries need limits. WHY: unbounded retries cause noisy failures. HOW TO APPLY: cap attempts at three.",
			"confidence": 0.8,
			"durability": "project",
			"tags": ["queue"],
		},
	]

	filtered = _filter_candidates(candidates)
	assert len(filtered) == 1
	assert filtered[0]["kind"] == "pitfall"


def test_to_dicts_normalizes_memory_candidates():
	"""_to_dicts should convert MemoryCandidate objects to plain dicts."""
	candidate = MemoryCandidate(
		primitive="decision",
		title="Use SQLite for session catalog",
		body="Use SQLite for the session catalog. WHY: lightweight, embedded. HOW TO APPLY: no external DB dependency.",
		confidence=0.9,
		tags=["database", "architecture"],
	)
	result = _to_dicts([candidate, {"primitive": "learning", "title": "test"}])
	assert len(result) == 2
	assert isinstance(result[0], dict)
	assert result[0]["primitive"] == "decision"
	assert result[0]["title"] == "Use SQLite for session catalog"
	assert isinstance(result[1], dict)
	assert result[1]["title"] == "test"


def test_to_dicts_skips_non_dict_non_candidate():
	"""_to_dicts should silently skip items that are neither dict nor MemoryCandidate."""
	result = _to_dicts(["string_item", 42, None])
	assert result == []


def test_pipeline_module_has_three_predictors():
	"""MemoryExtractionPipeline should have extract, consolidate, and quality_gate."""
	pipeline = MemoryExtractionPipeline()
	assert hasattr(pipeline, "extract")
	assert hasattr(pipeline, "consolidate")
	assert hasattr(pipeline, "quality_gate")


def test_consolidate_signature_fields():
	"""ConsolidateCandidatesSignature should have correct input/output fields."""
	fields = ConsolidateCandidatesSignature.model_fields
	assert "candidates" in fields
	assert "unique_candidates" in fields


def test_quality_gate_signature_fields():
	"""QualityGateSignature should have correct input/output fields."""
	fields = QualityGateSignature.model_fields
	assert "candidates" in fields
	assert "accepted" in fields
