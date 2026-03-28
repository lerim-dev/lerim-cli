"""Eval scoring dataclass and deterministic validation checks.

Provides EvalScore for recording per-trace eval results, composite score
computation, and schema/field validation for extraction and summarization outputs.
Also includes LerimBenchScore for full 7-dimension benchmark scoring with
deterministic check functions for dedup, search (NDCG), and archive precision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from lerim.memory.schemas import MemoryCandidate


@dataclass
class EvalScore:
	"""Per-trace eval result with deterministic checks and judge scores."""

	trace: str
	schema_ok: bool
	has_candidates: bool = False
	fields_present: bool = False
	word_limits: bool = False
	completeness: float = 0.0
	faithfulness: float = 0.0
	clarity: float = 0.0
	precision: float = 0.0
	composite: float = 0.0
	wall_time_s: float = 0.0
	judge_reasoning: str = ""
	candidate_count: int = 0


def compute_composite(
	completeness: float,
	faithfulness: float,
	clarity: float,
	precision: float = 1.0,
) -> float:
	"""Weighted average: precision 30%, completeness 25%, faithfulness 25%, clarity 20%.

	Precision gets highest weight because over-extraction is worse than
	under-extraction for memory systems. Default precision=1.0 preserves
	backward compatibility for callers that don't pass it.
	"""
	return precision * 0.30 + completeness * 0.25 + faithfulness * 0.25 + clarity * 0.20


def check_extraction_schema(output: list[dict]) -> bool:
	"""Validate each item in extraction output against MemoryCandidate schema."""
	if not isinstance(output, list) or not output:
		return False
	for item in output:
		try:
			MemoryCandidate.model_validate(item)
		except Exception:
			return False
	return True


def check_summarization_fields(output: dict) -> bool:
	"""Check that required frontmatter fields are present in summarization output."""
	required = {"title", "description", "user_intent", "session_narrative", "coding_agent"}
	return isinstance(output, dict) and required.issubset(output.keys())


def check_word_limits(output: dict) -> bool:
	"""Check user_intent <= 150 words, session_narrative <= 200 words."""
	if not isinstance(output, dict):
		return False
	intent_words = len(str(output.get("user_intent", "")).split())
	narrative_words = len(str(output.get("session_narrative", "")).split())
	return intent_words <= 150 and narrative_words <= 200


def check_extraction_quality(output: list[dict]) -> dict[str, Any]:
	"""Deterministic quality red flags for extraction output — no judge needed.

	Returns counts of common quality issues that indicate over-extraction
	or low-quality candidates.
	"""
	tautology = low_conf = session_dur = thin = 0
	for item in output:
		title = str(item.get("title", "")).lower().strip(".")
		body = str(item.get("body", "")).lower().strip(".")
		if title and body and (
			title == body
			or (body.startswith(title) and len(body) < len(title) + 20)
		):
			tautology += 1
		if isinstance(item.get("confidence"), (int, float)) and item["confidence"] < 0.5:
			low_conf += 1
		if str(item.get("durability", "")).lower() == "session":
			session_dur += 1
		if len(str(item.get("body", ""))) < 30:
			thin += 1
	return {
		"tautology_count": tautology,
		"low_confidence_count": low_conf,
		"session_durability_count": session_dur,
		"thin_body_count": thin,
		"total_quality_issues": tautology + low_conf + session_dur + thin,
	}


@dataclass
class LerimBenchScore:
	"""Full 7-dimension LerimBench score."""

	extraction_precision: float = 0.0
	extraction_recall: float = 0.0
	dedup_accuracy: float = 0.0
	consolidation_quality: float = 0.0  # LLM-as-judge
	archive_precision: float = 0.0
	search_relevance: float = 0.0  # NDCG@5
	scale_degradation: float = 1.0  # ratio, 1.0 = no degradation


def compute_lerim_bench_composite(score: LerimBenchScore) -> float:
	"""Weighted composite: extraction_precision 0.20, extraction_recall 0.20,
	dedup_accuracy 0.15, consolidation_quality 0.15, archive_precision 0.10,
	search_relevance 0.15, scale_degradation 0.05."""
	return (
		score.extraction_precision * 0.20
		+ score.extraction_recall * 0.20
		+ score.dedup_accuracy * 0.15
		+ score.consolidation_quality * 0.15
		+ score.archive_precision * 0.10
		+ score.search_relevance * 0.15
		+ score.scale_degradation * 0.05
	)


def _fuzzy_title_match(golden_title: str, pred_title: str) -> bool:
	"""Match titles using substring containment or keyword overlap."""
	g = golden_title.lower().strip()
	p = pred_title.lower().strip()
	# Exact
	if g == p:
		return True
	# Substring containment
	if g in p or p in g:
		return True
	# Keyword overlap (Jaccard > 0.5)
	g_words = set(g.split()) - {"the", "a", "an", "for", "to", "in", "of", "with", "and", "or"}
	p_words = set(p.split()) - {"the", "a", "an", "for", "to", "in", "of", "with", "and", "or"}
	if g_words and p_words:
		jaccard = len(g_words & p_words) / len(g_words | p_words)
		if jaccard > 0.5:
			return True
	return False


def check_dedup_accuracy(predictions: list[dict], golden: list[dict]) -> float:
	"""Compare predicted dedup classifications against golden assertions.

	Each item has candidate_title and expected_action (add/update/no_op).
	Uses fuzzy title matching to tolerate LLM-generated title variations.
	"""
	if not golden:
		return 1.0
	correct = 0
	for g in golden:
		title = g["candidate_title"]
		expected = g["expected_action"]
		# Find best matching prediction using fuzzy title match
		pred = next(
			(p for p in predictions if _fuzzy_title_match(title, p.get("candidate_title", ""))),
			None,
		)
		if pred and pred.get("action") == expected:
			correct += 1
	return correct / len(golden)


def compute_ndcg(ranked_results: list[str], relevant: set[str], k: int = 5) -> float:
	"""NDCG@k for search relevance."""
	dcg = 0.0
	for i, result in enumerate(ranked_results[:k]):
		rel = 1.0 if result in relevant else 0.0
		dcg += rel / math.log2(i + 2)
	# Ideal DCG
	ideal_count = min(len(relevant), k)
	idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))
	return dcg / idcg if idcg > 0 else 0.0


def check_archive_precision(
	archived: list[str], should_archive: set[str], should_keep: set[str]
) -> float:
	"""Precision of archive decisions. Penalizes archiving things that should be kept."""
	if not archived:
		return 1.0 if not should_archive else 0.0
	correct = sum(1 for a in archived if a in should_archive)
	incorrect = sum(1 for a in archived if a in should_keep)
	return correct / (correct + incorrect) if (correct + incorrect) > 0 else 0.0


def check_extraction_assertions(output: list[dict], assertions: dict) -> dict[str, float]:
	"""Score extraction output against golden assertions.

	Checks must_extract items via case-insensitive substring matching on
	title_contains and body_contains. Checks must_not_extract for precision
	penalties. Validates min/max_candidates bounds.

	Returns {"recall": float, "precision_penalty": float, "bounds_ok": bool, "score": float}.
	"""
	must_extract = assertions.get("must_extract", [])
	must_not_extract = assertions.get("must_not_extract", [])
	min_candidates = assertions.get("min_candidates", 0)
	max_candidates = assertions.get("max_candidates", 999)

	# Recall: how many must_extract items were found
	hits = 0
	for expected in must_extract:
		title_pat = expected.get("title_contains", "").lower()
		body_pat = expected.get("body_contains", "").lower()
		for item in output:
			title = str(item.get("title", "")).lower()
			body = str(item.get("body", "")).lower()
			title_match = title_pat in title if title_pat else True
			body_match = body_pat in body if body_pat else True
			if title_match and body_match:
				hits += 1
				break
	recall = hits / len(must_extract) if must_extract else 1.0

	# Precision penalty: how many must_not_extract items were found
	violations = 0
	for forbidden in must_not_extract:
		title_pat = forbidden.get("title_contains", "").lower()
		for item in output:
			title = str(item.get("title", "")).lower()
			if title_pat and title_pat in title:
				violations += 1
				break
	penalty = violations / len(output) if output else 0.0

	# Bounds check
	bounds_ok = min_candidates <= len(output) <= max_candidates

	# Composite score
	score = recall * (1.0 - penalty)
	if not bounds_ok:
		score *= 0.8  # 20% penalty for out-of-bounds candidate count

	return {
		"recall": round(recall, 4),
		"precision_penalty": round(penalty, 4),
		"bounds_ok": bounds_ok,
		"score": round(score, 4),
	}


def check_summarization_assertions(output: dict, assertions: dict) -> dict[str, float]:
	"""Score summary output against golden assertions.

	Checks required fields present, word limits, content substring matches.
	Returns {"fields_ok": bool, "limits_ok": bool, "content_score": float, "score": float}.
	"""
	if not isinstance(output, dict):
		return {"fields_ok": False, "limits_ok": False, "content_score": 0.0, "score": 0.0}

	# Fields check
	must_fields = assertions.get("must_contain_fields", [])
	fields_ok = all(output.get(f) for f in must_fields) if must_fields else True

	# Word limits
	max_narrative = assertions.get("max_narrative_words", 999)
	max_intent = assertions.get("max_intent_words", 999)
	narrative_words = len(str(output.get("session_narrative", "")).split())
	intent_words = len(str(output.get("user_intent", "")).split())
	limits_ok = narrative_words <= max_narrative and intent_words <= max_intent

	# Content checks (substring matching)
	content_checks = 0
	content_total = 0
	for key in ("title_contains", "coding_agent"):
		expected = assertions.get(key)
		if expected:
			content_total += 1
			actual_key = "title" if key == "title_contains" else "coding_agent"
			actual = str(output.get(actual_key, "")).lower()
			if expected.lower() in actual:
				content_checks += 1
	content_score = content_checks / content_total if content_total else 1.0

	# Composite
	score = (1.0 if fields_ok else 0.5) * (1.0 if limits_ok else 0.8) * content_score

	return {
		"fields_ok": fields_ok,
		"limits_ok": limits_ok,
		"content_score": round(content_score, 4),
		"score": round(score, 4),
	}


if __name__ == "__main__":
	"""Self-test for scoring utilities."""
	assert compute_composite(1.0, 1.0, 1.0, 1.0) == 1.0
	assert compute_composite(0.0, 0.0, 0.0, 0.0) == 0.0
	# precision=1.0 default: 0.30 + 0.8*0.25 + 0.6*0.25 + 0.4*0.20 = 0.30 + 0.20 + 0.15 + 0.08 = 0.73
	assert abs(compute_composite(0.8, 0.6, 0.4) - 0.73) < 1e-9

	assert check_extraction_schema([{"primitive": "decision", "title": "T", "body": "B"}])
	assert not check_extraction_schema([{"bad": "data"}])
	assert not check_extraction_schema([])

	assert check_summarization_fields({
		"title": "t", "description": "d", "user_intent": "u",
		"session_narrative": "s", "coding_agent": "c",
	})
	assert not check_summarization_fields({"title": "t"})

	assert check_word_limits({"user_intent": "a " * 150, "session_narrative": "b " * 200})
	assert not check_word_limits({"user_intent": "a " * 151, "session_narrative": "b"})

	quality = check_extraction_quality([
		{"title": "Same", "body": "Same", "confidence": 0.3, "durability": "session"},
		{"title": "Good title here", "body": "Good body with enough content to pass the check.", "confidence": 0.8},
	])
	assert quality["tautology_count"] == 1
	assert quality["low_confidence_count"] == 1
	assert quality["session_durability_count"] == 1
	assert quality["thin_body_count"] == 1  # "Same" is < 30 chars
	assert quality["total_quality_issues"] == 4

	score = EvalScore(trace="test.jsonl", schema_ok=True, has_candidates=True, precision=0.7)
	assert score.trace == "test.jsonl"
	assert score.precision == 0.7

	# LerimBenchScore tests
	bench = LerimBenchScore(
		extraction_precision=0.9, extraction_recall=0.8, dedup_accuracy=0.85,
		consolidation_quality=0.7, archive_precision=0.95,
		search_relevance=0.75, scale_degradation=0.9,
	)
	comp = compute_lerim_bench_composite(bench)
	expected_bench = (0.9 * 0.20 + 0.8 * 0.20 + 0.85 * 0.15 + 0.7 * 0.15
		+ 0.95 * 0.10 + 0.75 * 0.15 + 0.9 * 0.05)
	assert abs(comp - expected_bench) < 1e-9

	# check_dedup_accuracy tests
	golden = [
		{"candidate_title": "A", "expected_action": "add"},
		{"candidate_title": "B", "expected_action": "no_op"},
	]
	preds = [
		{"candidate_title": "A", "action": "add"},
		{"candidate_title": "B", "action": "add"},
	]
	assert abs(check_dedup_accuracy(preds, golden) - 0.5) < 1e-9
	assert check_dedup_accuracy([], []) == 1.0

	# compute_ndcg tests
	assert abs(compute_ndcg(["a", "b", "c"], {"a", "c"}, k=5) - (
		(1.0 / math.log2(2) + 0.0 / math.log2(3) + 1.0 / math.log2(4))
		/ (1.0 / math.log2(2) + 1.0 / math.log2(3))
	)) < 1e-9
	assert compute_ndcg([], {"a"}, k=5) == 0.0
	assert compute_ndcg(["a"], set(), k=5) == 0.0

	# check_archive_precision tests
	assert check_archive_precision(["a", "b"], {"a", "b"}, {"c"}) == 1.0
	assert check_archive_precision(["a", "c"], {"a"}, {"c"}) == 0.5
	assert check_archive_precision([], set(), set()) == 1.0
	assert check_archive_precision([], {"a"}, set()) == 0.0

	# check_extraction_assertions tests
	ext_output = [
		{"title": "Use JWT tokens", "body": "Auth uses JWT with HS256"},
		{"title": "Deploy to AWS", "body": "Production on ECS"},
	]
	ext_assertions = {
		"must_extract": [
			{"title_contains": "jwt", "body_contains": "hs256"},
			{"title_contains": "deploy"},
		],
		"must_not_extract": [{"title_contains": "database"}],
		"min_candidates": 1,
		"max_candidates": 5,
	}
	ext_result = check_extraction_assertions(ext_output, ext_assertions)
	assert ext_result["recall"] == 1.0
	assert ext_result["precision_penalty"] == 0.0
	assert ext_result["bounds_ok"] is True
	assert ext_result["score"] == 1.0

	# Partial recall
	ext_partial = check_extraction_assertions(
		[{"title": "Use JWT", "body": "token auth"}],
		{"must_extract": [{"title_contains": "jwt"}, {"title_contains": "deploy"}]},
	)
	assert ext_partial["recall"] == 0.5

	# Empty assertions
	assert check_extraction_assertions([], {})["score"] == 1.0

	# check_summarization_assertions tests
	sum_output = {
		"title": "JWT Auth Setup",
		"description": "Setting up authentication",
		"user_intent": "implement auth",
		"session_narrative": "The user worked on auth",
		"coding_agent": "claude",
	}
	sum_assertions = {
		"must_contain_fields": ["title", "description", "coding_agent"],
		"max_narrative_words": 50,
		"max_intent_words": 20,
		"title_contains": "jwt",
		"coding_agent": "claude",
	}
	sum_result = check_summarization_assertions(sum_output, sum_assertions)
	assert sum_result["fields_ok"] is True
	assert sum_result["limits_ok"] is True
	assert sum_result["content_score"] == 1.0
	assert sum_result["score"] == 1.0

	# Non-dict input
	assert check_summarization_assertions("not a dict", {})["score"] == 0.0

	print("scores: self-test passed")
