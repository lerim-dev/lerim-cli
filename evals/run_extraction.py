"""Run extraction eval across all traces in evals/traces/.

Loads eval config, runs the extraction pipeline on each trace, performs
deterministic schema checks, invokes an LLM judge for quality scoring,
and saves aggregated results to evals/results/.

Usage: python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml [--traces-dir evals/dataset/traces/]
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

from evals.judge import build_judge_prompt, invoke_judge
from evals.scores import (
    EvalScore,
    check_extraction_schema,
    compute_composite,
)


EVALS_DIR = Path(__file__).parent
TRACES_DIR = EVALS_DIR / "traces"
RESULTS_DIR = EVALS_DIR / "results"
JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "extraction.md"


def _configure_dspy_from_eval(config: dict) -> tuple:
    """Build isolated eval config for extraction. Returns (Config, temp_dir_path)."""
    REQUIRED_SECTIONS = ("lead", "explorer", "extraction", "summarization")
    missing = [s for s in REQUIRED_SECTIONS if s not in config]
    if missing:
        raise ValueError(
            f"Eval config missing required sections: {missing}. "
            f"All of {REQUIRED_SECTIONS} are required."
        )

    section_to_role = {
        "lead": "lead",
        "explorer": "explorer",
        "extraction": "extract",
        "summarization": "summarize",
    }
    roles_override = {
        role_name: config[section_name]
        for section_name, role_name in section_to_role.items()
    }

    temp_dir = Path(tempfile.mkdtemp(prefix="lerim_eval_extraction_"))
    (temp_dir / "memory").mkdir()
    (temp_dir / "index").mkdir()

    from lerim.config.settings import build_eval_config, set_config_override

    eval_cfg = build_eval_config(roles_override, temp_dir)
    set_config_override(eval_cfg)
    return eval_cfg, temp_dir


def run_extraction_eval(
    config_path: Path, traces_dir: Path | None = None, limit: int = 0
) -> dict:
    """Run extraction eval and return results dict."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = _configure_dspy_from_eval(config)

    try:
        from lerim.memory.extract_pipeline import extract_memories_from_session_file

        judge_agent = config.get("judge", {}).get("agent", "claude")
        judge_timeout = config.get("judge", {}).get("timeout_seconds", 300)
        effective_traces_dir = traces_dir or TRACES_DIR
        traces = sorted(effective_traces_dir.glob("*.jsonl")) + sorted(
            effective_traces_dir.glob("*.json")
        )
        traces = [t for t in traces if t.name != ".gitkeep"]
        if limit and limit > 0:
            traces = traces[:limit]

        if not traces:
            logger.warning("No traces found. Add .jsonl or .json trace files.")
            return {}

        per_trace: list[dict] = []
        total_start = time.time()

        for i, trace_path in enumerate(traces, 1):
            logger.info("[{}/{}] Evaluating: {}", i, len(traces), trace_path.name)
            t0 = time.time()

            # Run extraction pipeline
            try:
                output = extract_memories_from_session_file(trace_path)
            except Exception as e:
                logger.warning("Pipeline error: {}", e)
                per_trace.append(
                    EvalScore(
                        trace=trace_path.name,
                        schema_ok=False,
                        judge_reasoning=str(e),
                    ).__dict__
                )
                continue

            extract_time = time.time() - t0
            logger.info(
                "[{}/{}] Extraction done ({:.1f}s, {} candidates)",
                i, len(traces), extract_time, len(output),
            )

            # Deterministic checks
            schema_ok = check_extraction_schema(output)
            has_candidates = isinstance(output, list) and len(output) > 0

            # Judge scoring
            logger.info("[{}/{}] Judging...", i, len(traces))
            judge_start = time.time()
            try:
                output_json = json.dumps(output, indent=2, ensure_ascii=False)
                prompt = build_judge_prompt(JUDGE_PROMPT, trace_path, output_json)
                judge_result = invoke_judge(judge_agent, prompt, timeout=judge_timeout)
                completeness = float(judge_result.get("completeness", 0))
                faithfulness = float(judge_result.get("faithfulness", 0))
                clarity = float(judge_result.get("clarity", 0))
                reasoning = judge_result.get("reasoning", "")
            except Exception as e:
                logger.warning("Judge error: {}", e)
                completeness = faithfulness = clarity = 0.0
                reasoning = f"Judge failed: {e}"

            judge_time = time.time() - judge_start
            wall_time = time.time() - t0
            logger.info("[{}/{}] Judge done ({:.1f}s)", i, len(traces), judge_time)

            composite = compute_composite(completeness, faithfulness, clarity)

            score = EvalScore(
                trace=trace_path.name,
                schema_ok=schema_ok,
                has_candidates=has_candidates,
                completeness=completeness,
                faithfulness=faithfulness,
                clarity=clarity,
                composite=composite,
                wall_time_s=round(wall_time, 2),
                judge_reasoning=reasoning,
                candidate_count=len(output),
            )
            per_trace.append(score.__dict__)
            logger.success(
                "[{}/{}] schema={} candidates={} composite={:.2f} time={:.1f}s",
                i,
                len(traces),
                schema_ok,
                len(output),
                composite,
                wall_time,
            )

        total_wall = time.time() - total_start

        # Aggregate scores
        n = len(per_trace) or 1
        agg = {
            k: round(sum(t.get(k, 0) for t in per_trace) / n, 3)
            for k in (
                "completeness",
                "faithfulness",
                "clarity",
                "composite",
                "wall_time_s",
            )
        }
        agg["schema_ok"] = round(sum(1 for t in per_trace if t.get("schema_ok")) / n, 3)

        extraction_cfg = config.get("extraction", {})
        result = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pipeline": "extraction",
            "config": extraction_cfg,
            "judge": {"agent": judge_agent, "model": ""},
            "performance": {
                "total_wall_time_s": round(total_wall, 2),
                "avg_time_per_trace_s": round(total_wall / len(traces), 2)
                if traces
                else 0,
                "trace_count": len(traces),
            },
            "scores": agg,
            "per_trace": per_trace,
        }

        # Save results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"extraction_{ts}.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info("Results saved to: {}", out_path)

        # Print summary table
        print(
            f"\n{'Trace':<40} {'Schema':>6} {'Cands':>5} {'Compl':>6} {'Faith':>6} {'Clar':>6} {'COMP':>6} {'Time':>6}"
        )
        print("-" * 90)
        for t in per_trace:
            print(
                f"{t['trace']:<40} {'ok' if t['schema_ok'] else 'FAIL':>6} {t.get('candidate_count', 0):>5} "
                f"{t['completeness']:>6.2f} {t['faithfulness']:>6.2f} {t['clarity']:>6.2f} "
                f"{t['composite']:>6.2f} {t['wall_time_s']:>5.1f}s"
            )
        print("-" * 90)
        print(
            f"{'AVERAGE':<40} {agg['schema_ok']:>6.2f} {'':>5} "
            f"{agg['completeness']:>6.2f} {agg['faithfulness']:>6.2f} {agg['clarity']:>6.2f} "
            f"{agg['composite']:>6.2f} {agg['wall_time_s']:>5.1f}s"
        )

        return result
    finally:
        from lerim.config.settings import set_config_override

        set_config_override(None)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run extraction eval")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to eval config TOML (see evals/configs/ for examples)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max traces (0=all)")
    parser.add_argument(
        "--traces-dir",
        default=None,
        help="Override default traces directory (evals/traces/)",
    )
    args = parser.parse_args()
    td = Path(args.traces_dir) if args.traces_dir else None
    run_extraction_eval(Path(args.config), traces_dir=td, limit=args.limit)
