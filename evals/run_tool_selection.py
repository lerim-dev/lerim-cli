"""Run tool selection accuracy eval against golden datasets.

Pure deterministic evaluation -- no LLM judge needed. Parses OAI agent
traces to extract tool call sequences, compares against expected sequences
and must_not_call constraints, and computes accuracy metrics.

Usage: PYTHONPATH=. python evals/run_tool_selection.py \
  --golden-dir path/to/golden/tool_selection/
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from lerim.config.logging import logger

from evals.common import console, make_progress


EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"


def _extract_tool_calls(agent_trace: dict | list) -> list[dict]:
	"""Parse OAI agent trace to extract tool call sequence.

	Handles both list-of-events and dict-with-events formats.
	Returns list of {"step": int, "tool": str, "args": dict, "result_status": str}.
	"""
	events = agent_trace if isinstance(agent_trace, list) else agent_trace.get("events", [])
	calls: list[dict] = []
	step = 0

	for event in events:
		if not isinstance(event, dict):
			continue
		# Handle function_call events
		event_type = event.get("type", "")
		if event_type == "function_call" or "function" in event:
			step += 1
			func = event.get("function", event.get("name", ""))
			if isinstance(func, dict):
				name = func.get("name", "")
				args = func.get("arguments", {})
			else:
				name = str(func)
				args = event.get("arguments", event.get("args", {}))
			if isinstance(args, str):
				try:
					args = json.loads(args)
				except (json.JSONDecodeError, TypeError):
					args = {"raw": args}
			calls.append({
				"step": step,
				"tool": name,
				"args": args if isinstance(args, dict) else {},
			})
		# Handle tool_calls in message content
		elif event_type in ("message", "response"):
			tool_calls = event.get("tool_calls", [])
			if not tool_calls and isinstance(event.get("content"), list):
				tool_calls = [
					c for c in event["content"]
					if isinstance(c, dict) and c.get("type") == "function_call"
				]
			for tc in tool_calls:
				step += 1
				if isinstance(tc, dict):
					name = tc.get("name", tc.get("function", {}).get("name", ""))
					args = tc.get("arguments", tc.get("function", {}).get("arguments", {}))
					if isinstance(args, str):
						try:
							args = json.loads(args)
						except (json.JSONDecodeError, TypeError):
							args = {"raw": args}
					calls.append({
						"step": step,
						"tool": name,
						"args": args if isinstance(args, dict) else {},
					})
		# Handle tool_result events for retry_rate
		elif event_type in ("function_result", "tool_result"):
			result = event.get("result", event.get("output", ""))
			if calls:
				status = "error" if str(result).startswith("ERROR") else "ok"
				calls[-1]["result_status"] = status

	return calls


def _compute_sequence_accuracy(
	actual: list[dict], expected: list[dict]
) -> float:
	"""Order-aware comparison of tool call sequences.

	Compares expected steps against actual calls. A step matches if the
	tool name matches at the same position in the sequence.
	"""
	if not expected:
		return 1.0
	matches = 0
	for exp in expected:
		step_idx = exp["step"] - 1  # 1-based to 0-based
		if step_idx < len(actual) and actual[step_idx]["tool"] == exp["tool"]:
			matches += 1
	return matches / len(expected)


def _check_must_not_call(
	actual: list[dict], must_not_call: list[str]
) -> tuple[float, list[str]]:
	"""Check for forbidden tool calls. Returns (score, violations).

	Score is 1.0 if no violations, reduced by each violation.
	"""
	if not must_not_call:
		return 1.0, []
	called_tools = {c["tool"] for c in actual}
	violations = [t for t in must_not_call if t in called_tools]
	if not violations:
		return 1.0, []
	return max(0.0, 1.0 - len(violations) / len(must_not_call)), violations


def _compute_retry_rate(actual: list[dict]) -> float:
	"""Fraction of tool calls that resulted in ERROR status."""
	if not actual:
		return 0.0
	errors = sum(1 for c in actual if c.get("result_status") == "error")
	return errors / len(actual)


def run_tool_selection_eval(
	golden_dir: Path,
	limit: int = 0,
) -> dict:
	"""Run tool selection eval across golden cases and return results dict."""
	cases = sorted(
		d for d in golden_dir.iterdir()
		if d.is_dir() and (d / "expected" / "assertions.json").exists()
	)
	if limit and limit > 0:
		cases = cases[:limit]

	if not cases:
		logger.warning("No golden tool_selection cases found in {}", golden_dir)
		return {}

	per_case: list[dict] = []
	total_start = time.time()

	with make_progress() as progress:
		task = progress.add_task("Tool selection eval", total=len(cases))

		for i, case_dir in enumerate(cases, 1):
			progress.update(task, description=f"[tools] {case_dir.name}")
			t0 = time.time()

			# Load agent trace
			trace_path = case_dir / "input" / "agent_trace.json"
			if not trace_path.exists():
				logger.warning("No agent_trace.json in {}", case_dir / "input")
				progress.advance(task)
				continue

			agent_trace = json.loads(trace_path.read_text(encoding="utf-8"))

			# Load assertions
			assertions = json.loads(
				(case_dir / "expected" / "assertions.json").read_text(encoding="utf-8")
			)
			expected_sequence = assertions.get("expected_sequence", [])
			must_not_call = assertions.get("must_not_call", [])

			# Extract actual tool calls
			actual_calls = _extract_tool_calls(agent_trace)

			# Compute metrics
			sequence_acc = _compute_sequence_accuracy(actual_calls, expected_sequence)
			violation_score, violations = _check_must_not_call(actual_calls, must_not_call)
			retry_rate = _compute_retry_rate(actual_calls)

			# Combined tool_selection_accuracy
			tool_selection_accuracy = sequence_acc * 0.7 + violation_score * 0.3

			wall_time = time.time() - t0

			logger.success(
				"[{}/{}] seq_acc={:.2f} violations={} retry_rate={:.2f} time={:.1f}s",
				i, len(cases), sequence_acc, len(violations), retry_rate, wall_time,
			)

			per_case.append({
				"case": case_dir.name,
				"tool_selection_accuracy": round(tool_selection_accuracy, 4),
				"sequence_accuracy": round(sequence_acc, 4),
				"violation_score": round(violation_score, 4),
				"violations": violations,
				"retry_rate": round(retry_rate, 4),
				"actual_tool_count": len(actual_calls),
				"expected_step_count": len(expected_sequence),
				"actual_tools": [c["tool"] for c in actual_calls],
				"wall_time_s": round(wall_time, 4),
			})
			progress.advance(task)

	total_wall = time.time() - total_start

	# Aggregate
	n = len(per_case) or 1
	agg = {
		"tool_selection_accuracy": round(
			sum(c.get("tool_selection_accuracy", 0) for c in per_case) / n, 4
		),
		"sequence_accuracy": round(
			sum(c.get("sequence_accuracy", 0) for c in per_case) / n, 4
		),
		"avg_retry_rate": round(
			sum(c.get("retry_rate", 0) for c in per_case) / n, 4
		),
		"total_violations": sum(len(c.get("violations", [])) for c in per_case),
	}

	result = {
		"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		"pipeline": "tool_selection",
		"performance": {
			"total_wall_time_s": round(total_wall, 2),
			"case_count": len(cases),
		},
		"scores": agg,
		"per_case": per_case,
	}

	RESULTS_DIR.mkdir(parents=True, exist_ok=True)
	ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
	out_path = RESULTS_DIR / f"tool_selection_{ts}.json"
	out_path.write_text(
		json.dumps(result, indent=2, ensure_ascii=False) + "\n",
		encoding="utf-8",
	)
	console.print(f"\nResults saved to: [bold]{out_path}[/]")
	console.print(
		f"  Tool selection accuracy: [bold]{agg['tool_selection_accuracy']:.4f}[/]  "
		f"Sequence accuracy: {agg['sequence_accuracy']:.4f}  "
		f"Violations: {agg['total_violations']}  "
		f"Time: {total_wall:.1f}s"
	)

	return result


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Run tool selection accuracy eval")
	parser.add_argument(
		"--golden-dir",
		required=True,
		help="Path to golden dataset directory with case subdirectories",
	)
	parser.add_argument("--limit", type=int, default=0, help="Max cases (0=all)")
	args = parser.parse_args()
	run_tool_selection_eval(Path(args.golden_dir), limit=args.limit)
