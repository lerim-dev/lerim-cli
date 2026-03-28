"""Run isolated maintain eval against golden datasets.

Loads golden cases with pre-populated memory stores, runs maintain,
and checks archive/merge decisions against golden assertions.
Computes archive_precision and consolidation_quality scores.

Usage: PYTHONPATH=. python evals/run_maintain.py \
  --config evals/configs/eval_minimax_m25.toml \
  --golden-dir path/to/golden/maintain/
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
from evals.scores import check_archive_precision, compute_composite


EVALS_DIR = Path(__file__).parent
RESULTS_DIR = EVALS_DIR / "results"
JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "maintain_isolated.md"


def _configure_from_eval(config: dict) -> tuple:
	"""Build isolated eval config for maintain eval. Returns (Config, temp_dir)."""
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

	temp_dir = Path(tempfile.mkdtemp(prefix="lerim_eval_maintain_"))
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


def _count_memory_files(memory_root: Path) -> int:
	"""Count .md files in decisions/ and learnings/ subdirs."""
	count = 0
	for subdir in ("decisions", "learnings"):
		d = memory_root / subdir
		if d.exists():
			count += sum(1 for f in d.iterdir() if f.suffix == ".md")
	return count


def _list_archived_ids(memory_root: Path) -> list[str]:
	"""List memory IDs that were archived (exist in archived/ subdirs)."""
	archived = []
	for subdir in ("decisions", "learnings"):
		d = memory_root / "archived" / subdir
		if not d.exists():
			continue
		for f in d.iterdir():
			if f.suffix == ".md":
				archived.append(f.stem)
	return archived


def _build_judge_prompt(
	memory_root: Path,
	run_folder: Path,
	before_count: int,
	after_count: int,
	assertions: dict,
) -> str:
	"""Build judge prompt for isolated maintain evaluation."""
	template = JUDGE_PROMPT.read_text(encoding="utf-8")
	return (
		template.replace("{memory_root}", str(memory_root))
		.replace("{run_folder}", str(run_folder))
		.replace("{before_count}", str(before_count))
		.replace("{after_count}", str(after_count))
		.replace("{assertions}", json.dumps(assertions, indent=2))
	)


def run_maintain_eval(
	config_path: Path,
	golden_dir: Path,
	limit: int = 0,
) -> dict:
	"""Run isolated maintain eval across golden cases and return results dict."""
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
			task = progress.add_task("Maintain eval", total=len(cases))

			for i, case_dir in enumerate(cases, 1):
				progress.update(task, description=f"[maintain] {case_dir.name}")
				t0 = time.time()

				# Reset memory store for each case
				for subdir in ("decisions", "learnings", "archived/decisions", "archived/learnings"):
					d = memory_root / subdir
					if d.exists():
						shutil.rmtree(d)
					d.mkdir(parents=True)

				# Copy golden memory store
				input_store = case_dir / "input" / "memory_store"
				if input_store.is_dir():
					_copy_memory_store(input_store, memory_root)

				memory_before = _count_memory_files(memory_root)

				# Load golden assertions
				assertions = json.loads(
					(case_dir / "expected" / "assertions.json").read_text(encoding="utf-8")
				)
				should_archive = set(assertions.get("should_archive", []))
				should_merge = assertions.get("should_merge", [])
				should_keep = set(assertions.get("should_keep", []))

				# Run maintain
				try:
					agent = LerimOAIAgent(config=eval_cfg, default_cwd=str(Path.cwd()))
					result = agent.maintain(
						memory_root=str(memory_root),
						workspace_root=str(workspace_root),
					)
				except Exception as e:
					logger.warning("[{}/{}] Maintain error: {}", i, len(cases), e)
					per_case.append({
						"case": case_dir.name,
						"archive_precision": 0.0,
						"composite": 0.0,
						"wall_time_s": round(time.time() - t0, 2),
						"judge_reasoning": str(e),
					})
					progress.advance(task)
					continue

				wall_time = time.time() - t0
				memory_after = _count_memory_files(memory_root)
				run_folder = Path(result.get("run_folder", ""))

				# Check archive precision
				archived_ids = _list_archived_ids(memory_root)
				archive_prec = check_archive_precision(
					archived_ids, should_archive, should_keep
				)

				# Judge scoring
				completeness = faithfulness = coherence = precision = 0.0
				reasoning = ""
				if JUDGE_PROMPT.exists():
					try:
						progress.update(task, description=f"[judge] {case_dir.name}")
						prompt = _build_judge_prompt(
							memory_root, run_folder, memory_before, memory_after,
							assertions,
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
					"[{}/{}] archive_prec={:.2f} composite={:.2f} {}>{}  time={:.0f}s",
					i, len(cases), archive_prec, composite,
					memory_before, memory_after, wall_time,
				)

				per_case.append({
					"case": case_dir.name,
					"memory_before": memory_before,
					"memory_after": memory_after,
					"archived_count": len(archived_ids),
					"archive_precision": round(archive_prec, 3),
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
			"archive_precision": round(
				sum(c.get("archive_precision", 0) for c in per_case) / n, 3
			),
			"composite": round(
				sum(c.get("composite", 0) for c in per_case) / n, 3
			),
		}

		result_out = {
			"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
			"pipeline": "maintain",
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
		out_path = RESULTS_DIR / f"maintain_{ts}.json"
		out_path.write_text(
			json.dumps(result_out, indent=2, ensure_ascii=False) + "\n",
			encoding="utf-8",
		)
		console.print(f"\nResults saved to: [bold]{out_path}[/]")
		console.print(
			f"  Archive precision: [bold]{agg['archive_precision']:.3f}[/]  "
			f"Composite: [bold]{agg['composite']:.3f}[/]  "
			f"Time: {total_wall:.0f}s"
		)

		return result_out
	finally:
		cleanup_eval(temp_dir)


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description="Run isolated maintain eval")
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
	run_maintain_eval(Path(args.config), Path(args.golden_dir), limit=args.limit)
