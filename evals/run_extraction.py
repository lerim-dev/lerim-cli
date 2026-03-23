"""Run extraction eval across all traces in evals/traces/.

Loads eval config, runs the extraction pipeline on each trace, performs
deterministic schema checks, invokes an LLM judge for quality scoring,
and saves aggregated results to evals/results/.

Usage: python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml [--traces-dir evals/dataset/traces/]
"""

from __future__ import annotations

import argparse
import json
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from lerim.config.logging import logger

from evals.common import (
    cleanup_eval,
    configure_dspy_from_eval,
    console,
    make_progress,
    print_extraction_table,
)
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


def run_extraction_eval(
    config_path: Path, traces_dir: Path | None = None, limit: int = 0
) -> dict:
    """Run extraction eval and return results dict."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = configure_dspy_from_eval(
        config, prefix="lerim_eval_extraction_"
    )

    try:
        from lerim.memory.extract_pipeline import extract_memories_from_session_file

        judge_agent = config.get("judge", {}).get("agent", "claude")
        judge_timeout = config.get("judge", {}).get("timeout_seconds", 300)
        judge_model = config.get("judge", {}).get("model")
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

        with make_progress() as progress:
            task = progress.add_task("Extraction", total=len(traces))

            for i, trace_path in enumerate(traces, 1):
                progress.update(task, description=f"[extract] {trace_path.name}")
                t0 = time.time()

                # Run extraction pipeline
                try:
                    output = extract_memories_from_session_file(trace_path)
                except Exception as e:
                    logger.warning("Pipeline error on {}: {}", trace_path.name, e)
                    per_trace.append(
                        EvalScore(
                            trace=trace_path.name,
                            schema_ok=False,
                            judge_reasoning=str(e),
                        ).__dict__
                    )
                    progress.advance(task)
                    continue

                extract_time = time.time() - t0
                logger.info(
                    "[{}/{}] Extracted {} candidates ({:.1f}s)",
                    i,
                    len(traces),
                    len(output),
                    extract_time,
                )

                # Deterministic checks
                schema_ok = check_extraction_schema(output)
                has_candidates = isinstance(output, list) and len(output) > 0

                # Judge scoring
                progress.update(task, description=f"[judge] {trace_path.name}")
                judge_start = time.time()
                try:
                    output_json = json.dumps(output, indent=2, ensure_ascii=False)
                    prompt = build_judge_prompt(JUDGE_PROMPT, trace_path, output_json)
                    judge_result = invoke_judge(
                        judge_agent, prompt, timeout=judge_timeout, model=judge_model
                    )
                    completeness = float(judge_result.get("completeness", 0))
                    faithfulness = float(judge_result.get("faithfulness", 0))
                    clarity = float(judge_result.get("clarity", 0))
                    precision = float(judge_result.get("precision", 0))
                    reasoning = judge_result.get("reasoning", "")
                except Exception as e:
                    logger.warning("Judge error on {}: {}", trace_path.name, e)
                    completeness = faithfulness = clarity = precision = 0.0
                    reasoning = f"Judge failed: {e}"

                judge_time = time.time() - judge_start
                wall_time = time.time() - t0

                composite = compute_composite(completeness, faithfulness, clarity, precision)

                score = EvalScore(
                    trace=trace_path.name,
                    schema_ok=schema_ok,
                    has_candidates=has_candidates,
                    completeness=completeness,
                    faithfulness=faithfulness,
                    clarity=clarity,
                    precision=precision,
                    composite=composite,
                    wall_time_s=round(wall_time, 2),
                    judge_reasoning=reasoning,
                    candidate_count=len(output),
                )
                per_trace.append(score.__dict__)
                logger.success(
                    "[{}/{}] schema={} cands={} comp={:.2f} ({:.0f}s extract, {:.0f}s judge)",
                    i,
                    len(traces),
                    schema_ok,
                    len(output),
                    composite,
                    extract_time,
                    judge_time,
                )
                progress.advance(task)

        total_wall = time.time() - total_start

        # Aggregate scores
        n = len(per_trace) or 1
        agg = {
            k: round(sum(t.get(k, 0) for t in per_trace) / n, 3)
            for k in (
                "completeness",
                "faithfulness",
                "clarity",
                "precision",
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
            "judge": {"agent": judge_agent, "model": judge_model or ""},
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
        console.print(f"\nResults saved to: [bold]{out_path}[/]")

        # Print summary table
        print_extraction_table(per_trace, agg)

        return result
    finally:
        cleanup_eval(temp_dir)


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
