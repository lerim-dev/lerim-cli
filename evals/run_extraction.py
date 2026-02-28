"""Run extraction eval across all traces in evals/traces/.

Loads eval config, runs the extraction pipeline on each trace, performs
deterministic schema checks, invokes an LLM judge for quality scoring,
and saves aggregated results to evals/results/.

Usage: python evals/run_extraction.py --config evals/eval_config.toml
"""

from __future__ import annotations

import argparse
import json
import os
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

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


def _configure_dspy_from_eval(config: dict) -> None:
    """Override lerim DSPy config via environment for the extraction role."""
    section = config.get("extraction", {})
    if section.get("provider"):
        os.environ["LERIM_EVAL_EXTRACT_PROVIDER"] = section["provider"]
    if section.get("model"):
        os.environ["LERIM_EVAL_EXTRACT_MODEL"] = section["model"]

    # Patch lerim config by writing a temporary override config
    from lerim.config.settings import reload_config, save_config_patch

    patch = {"roles": {"extract": {
        "provider": section.get("provider", "openrouter"),
        "model": section.get("model", "qwen/qwen3-coder-30b-a3b-instruct"),
        "sub_provider": section.get("sub_provider", section.get("provider", "openrouter")),
        "sub_model": section.get("sub_model", section.get("model", "qwen/qwen3-coder-30b-a3b-instruct")),
    }}}
    save_config_patch(patch)
    reload_config()


def run_extraction_eval(config_path: Path) -> dict:
    """Run extraction eval and return results dict."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    _configure_dspy_from_eval(config)

    from lerim.memory.extract_pipeline import extract_memories_from_session_file

    judge_agent = config.get("judge", {}).get("agent", "claude")
    traces = sorted(TRACES_DIR.glob("*.jsonl")) + sorted(TRACES_DIR.glob("*.json"))
    traces = [t for t in traces if t.name != ".gitkeep"]

    if not traces:
        print("No traces found in evals/traces/. Add .jsonl or .json trace files.")
        return {}

    per_trace: list[dict] = []
    total_start = time.time()

    for trace_path in traces:
        print(f"  Evaluating: {trace_path.name}")
        t0 = time.time()

        # Run extraction pipeline
        try:
            output = extract_memories_from_session_file(trace_path)
        except Exception as e:
            print(f"    Pipeline error: {e}")
            per_trace.append(EvalScore(
                trace=trace_path.name, schema_ok=False, judge_reasoning=str(e),
            ).__dict__)
            continue

        wall_time = time.time() - t0

        # Deterministic checks
        schema_ok = check_extraction_schema(output)
        has_candidates = isinstance(output, list) and len(output) > 0

        # Judge scoring
        try:
            output_json = json.dumps(output, indent=2, ensure_ascii=False)
            prompt = build_judge_prompt(JUDGE_PROMPT, trace_path, output_json)
            judge_result = invoke_judge(judge_agent, prompt)
            completeness = float(judge_result.get("completeness", 0))
            faithfulness = float(judge_result.get("faithfulness", 0))
            clarity = float(judge_result.get("clarity", 0))
            reasoning = judge_result.get("reasoning", "")
        except Exception as e:
            print(f"    Judge error: {e}")
            completeness = faithfulness = clarity = 0.0
            reasoning = f"Judge failed: {e}"

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
        print(f"    schema={schema_ok} candidates={len(output)} composite={composite:.2f} time={wall_time:.1f}s")

    total_wall = time.time() - total_start

    # Aggregate scores
    n = len(per_trace) or 1
    agg = {
        k: round(sum(t.get(k, 0) for t in per_trace) / n, 3)
        for k in ("completeness", "faithfulness", "clarity", "composite", "wall_time_s")
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
            "avg_time_per_trace_s": round(total_wall / len(traces), 2) if traces else 0,
            "trace_count": len(traces),
        },
        "scores": agg,
        "per_trace": per_trace,
    }

    # Save results
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"extraction_{ts}.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nResults saved to: {out_path}")

    # Print summary table
    print(f"\n{'Trace':<40} {'Schema':>6} {'Cands':>5} {'Compl':>6} {'Faith':>6} {'Clar':>6} {'COMP':>6} {'Time':>6}")
    print("-" * 90)
    for t in per_trace:
        print(f"{t['trace']:<40} {'ok' if t['schema_ok'] else 'FAIL':>6} {t.get('candidate_count', 0):>5} "
              f"{t['completeness']:>6.2f} {t['faithfulness']:>6.2f} {t['clarity']:>6.2f} "
              f"{t['composite']:>6.2f} {t['wall_time_s']:>5.1f}s")
    print("-" * 90)
    print(f"{'AVERAGE':<40} {agg['schema_ok']:>6.2f} {'':>5} "
          f"{agg['completeness']:>6.2f} {agg['faithfulness']:>6.2f} {agg['clarity']:>6.2f} "
          f"{agg['composite']:>6.2f} {agg['wall_time_s']:>5.1f}s")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run extraction eval")
    parser.add_argument("--config", default="evals/eval_config.toml", help="Path to eval config TOML")
    args = parser.parse_args()
    run_extraction_eval(Path(args.config))
