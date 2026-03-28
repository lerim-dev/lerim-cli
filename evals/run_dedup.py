"""Run dedup accuracy eval against golden datasets.

Loads golden cases from --golden-dir, runs sync on each trace with a
pre-populated memory store, and compares predicted dedup actions against
golden assertions. Optionally invokes an LLM judge for quality scoring.

Usage: PYTHONPATH=. python evals/run_dedup.py \
  --config evals/configs/eval_minimax_m25.toml \
  --golden-dir path/to/golden/dedup/
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from lerim.config.logging import logger

from evals.common import cleanup_eval, console, make_progress
from evals.judge import JUDGE_SCHEMA_COHERENCE, invoke_judge
from evals.scores import EvalScore, check_dedup_accuracy, compute_composite


EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"
JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "dedup.md"


def _configure_from_eval(config: dict) -> tuple:
	"""Build isolated eval config for dedup eval. Returns (Config, temp_dir)."""
	REQUIRED_SECTIONS = ("lead", "extraction", "summarization")
	missing = [s for s in REQUIRED_SECTIONS if s not in config]
	if missing:
		raise ValueError(
			f"Eval config missing required sections: {missing}. "
			f"All of {REQUIRED_SECTIONS} are required."
		)

	section_to_role = {
		"lead": "lead",
		"extraction": "extract",
		"summarization": "summarize",
	}
	roles_override = {
		role_name: config[section_name]
		for section_name, role_name in section_to_role.items()
	}

	temp_dir = Path(tempfile.mkdtemp(prefix="lerim_eval_dedup_"))
	for subdir in (
		"memory/decisions",
		"memory/learnings",
		"memory/summaries",
		"memory/archived/decisions",
		"memory/archived/learnings",
		"workspace",
		"index",
	):
		(temp_dir / subdir).mkdir(parents=True, exist_ok=True)

	from lerim.config.settings import build_eval_config, set_config_override

	eval_cfg = build_eval_config(roles_override, temp_dir)
	set_config_override(eval_cfg)
	return eval_cfg, temp_dir


def _copy_memory_store(source: Path, dest: Path) -> None:
	"""Copy a golden memory_store directory into the eval temp memory root."""
	for subdir in ("decisions", "learnings"):
		src = source / subdir
		dst = dest / subdir
		if src.is_dir():
			shutil.copytree(src, dst, dirs_exist_ok=True)


def _load_assertions(case_dir: Path) -> dict:
	"""Load expected/assertions.json from a golden case."""
	path = case_dir / "expected" / "assertions.json"
	return json.loads(path.read_text(encoding="utf-8"))


def _extract_actions_from_result(result: dict) -> list[dict]:
	"""Extract dedup action predictions from sync result artifacts."""
	artifacts = result.get("artifacts", {})
	actions_path = artifacts.get("memory_actions", "")
	if actions_path and Path(actions_path).exists():
		data = json.loads(Path(actions_path).read_text(encoding="utf-8"))
		if isinstance(data, list):
			return data
		if isinstance(data, dict):
			return data.get("actions", data.get("candidates", []))
	return []


def _build_judge_prompt(
	trace_path: Path,
	memory_root: Path,
	predictions: list[dict],
	golden: list[dict],
) -> str:
	"""Build judge prompt for dedup evaluation."""
	template = JUDGE_PROMPT.read_text(encoding="utf-8")
	return (
		template.replace("{trace_path}", str(trace_path))
		.replace("{memory_root}", str(memory_root))
		.replace("{predictions}", json.dumps(predictions, indent=2))
		.replace("{golden}", json.dumps(golden, indent=2))
	)


def run_dedup_eval(
	config_path: Path,
	golden_dir: Path,
	limit: int = 0,
) -> dict:
	"""Run dedup eval across golden cases and return results dict."""
	with config_path.open("rb") as f:
		config = tomllib.load(f)

	eval_cfg, temp_dir = _configure_from_eval(config)
	memory_root = temp_dir / "memory"
	workspace_root = temp_dir / "workspace"

	try:
		from lerim.runtime.oai_agent import LerimOAIAgent

		judge_agent = config.get("judge", {}).get("agent", "claude")
		judge_timeout = config.get("judge", {}).get("timeout_seconds", 300)
		judge_model = config.get("judge", {}).get("model")

		cases = sorted(
			d for d in golden_dir.iterdir()
			if d.is_dir() and (d / "expected" / "assertions.json").exists()
		)
		if limit and limit > 0:
			cases = cases[:limit]

		if not cases:
			logger.warning("No golden cases found in {}", golden_dir)
			return {}

		per_case: list[dict] = []
		total_start = time.time()

		with make_progress() as progress:
			task = progress.add_task("Dedup eval", total=len(cases))

			for i, case_dir in enumerate(cases, 1):
				progress.update(task, description=f"[dedup] {case_dir.name}")
				t0 = time.time()

				# Reset memory store for each case
				for subdir in ("decisions", "learnings"):
					d = memory_root / subdir
					if d.exists():
						shutil.rmtree(d)
					d.mkdir(parents=True)

				# Copy golden memory store
				input_store = case_dir / "input" / "memory_store"
				if input_store.is_dir():
					_copy_memory_store(input_store, memory_root)

				# Find trace file
				input_dir = case_dir / "input"
				traces = list(input_dir.glob("trace.*"))
				if not traces:
					logger.warning("No trace file in {}", input_dir)
					progress.advance(task)
					continue
				trace_path = traces[0]

				# Load golden assertions
				assertions = _load_assertions(case_dir)
				dedup_golden = assertions.get("dedup_assertions", [])

				# Run sync
				try:
					agent = LerimOAIAgent(config=eval_cfg, default_cwd=str(Path.cwd()))
					result = agent.sync(
						trace_path,
						memory_root=str(memory_root),
						workspace_root=str(workspace_root),
					)
				except Exception as e:
					logger.warning("[{}/{}] Sync error: {}", i, len(cases), e)
					per_case.append(EvalScore(
						trace=case_dir.name,
						schema_ok=False,
						judge_reasoning=str(e),
					).__dict__)
					progress.advance(task)
					continue

				wall_time = time.time() - t0

				# Extract predicted actions
				predictions = _extract_actions_from_result(result)

				# Deterministic dedup accuracy
				dedup_acc = check_dedup_accuracy(predictions, dedup_golden)

				# Judge scoring (optional)
				completeness = faithfulness = coherence = precision = 0.0
				reasoning = ""
				if JUDGE_PROMPT.exists():
					try:
						progress.update(task, description=f"[judge] {case_dir.name}")
						prompt = _build_judge_prompt(
							trace_path, memory_root, predictions, dedup_golden,
						)
						judge_result = invoke_judge(
							judge_agent, prompt,
							timeout=judge_timeout,
							model=judge_model,
							schema=JUDGE_SCHEMA_COHERENCE,
						)
						completeness = float(judge_result.get("completeness", 0))
						faithfulness = float(judge_result.get("faithfulness", 0))
						coherence = float(judge_result.get("coherence", 0))
						precision = float(judge_result.get("precision", 0))
						reasoning = judge_result.get("reasoning", "")
					except Exception as e:
						logger.warning("Judge error on {}: {}", case_dir.name, e)
						reasoning = f"Judge failed: {e}"

				composite = compute_composite(completeness, faithfulness, coherence, precision)

				logger.success(
					"[{}/{}] dedup_acc={:.2f} composite={:.2f} time={:.0f}s",
					i, len(cases), dedup_acc, composite, wall_time,
				)

				per_case.append({
					"case": case_dir.name,
					"trace": trace_path.name,
					"dedup_accuracy": round(dedup_acc, 3),
					"completeness": completeness,
					"faithfulness": faithfulness,
					"coherence": coherence,
					"precision": precision,
					"composite": composite,
					"wall_time_s": round(wall_time, 2),
					"judge_reasoning": reasoning,
				})
				progress.advance(task)

		total_wall = time.time() - total_start

		# Aggregate
		n = len(per_case) or 1
		agg = {
			"dedup_accuracy": round(
				sum(c.get("dedup_accuracy", 0) for c in per_case) / n, 3
			),
			"composite": round(
				sum(c.get("composite", 0) for c in per_case) / n, 3
			),
		}

		result_out = {
			"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
			"pipeline": "dedup",
			"config": {s: config.get(s, {}) for s in ("lead", "extraction", "summarization")},
			"judge": {"agent": judge_agent, "model": judge_model or ""},
			"performance": {
				"total_wall_time_s": round(total_wall, 2),
				"case_count": len(cases),
			},
			"scores": agg,
			"per_case": per_case,
		}

		RESULTS_DIR.mkdir(parents=True, exist_ok=True)
		ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
		out_path = RESULTS_DIR / f"dedup_{ts}.json"
		out_path.write_text(
			json.dumps(result_out, indent=2, ensure_ascii=False) + "\n",
			encoding="utf-8",
		)
		console.print(f"\nResults saved to: [bold]{out_path}[/]")
		console.print(
			f"  Dedup accuracy: [bold]{agg['dedup_accuracy']:.3f}[/]  "
			f"Composite: [bold]{agg['composite']:.3f}[/]  "
			f"Time: {total_wall:.0f}s"
		)

		return result_out
	finally:
		cleanup_eval(temp_dir)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Run dedup accuracy eval")
	parser.add_argument(
		"--config",
		required=True,
		help="Path to eval config TOML (see evals/configs/ for examples)",
	)
	parser.add_argument(
		"--golden-dir",
		required=True,
		help="Path to golden dataset directory with case subdirectories",
	)
	parser.add_argument("--limit", type=int, default=0, help="Max cases (0=all)")
	args = parser.parse_args()
	run_dedup_eval(Path(args.config), Path(args.golden_dir), limit=args.limit)
