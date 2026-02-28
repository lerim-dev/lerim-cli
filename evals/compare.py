"""Compare eval results across multiple runs and configs.

Reads all JSON result files from evals/results/, groups by pipeline,
and prints a comparison table showing scores, timings, and config info.

Usage: python evals/compare.py [--pipeline extraction|summarization]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


RESULTS_DIR = Path(__file__).parent / "results"


def _model_label(config: dict) -> str:
    """Build a short label from config model name and provider."""
    model = config.get("model", "unknown")
    provider = config.get("provider", "")
    short_model = model.split("/")[-1] if "/" in model else model
    return f"{short_model} ({provider})" if provider else short_model


def compare_results(pipeline_filter: str | None = None) -> None:
    """Load all result JSONs and print comparison table."""
    if not RESULTS_DIR.exists():
        print("No results directory found. Run evals first.")
        return

    result_files = sorted(RESULTS_DIR.glob("*.json"))
    if not result_files:
        print("No result files found in evals/results/.")
        return

    # Group by pipeline
    groups: dict[str, list[dict]] = {}
    for path in result_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        pipeline = data.get("pipeline", "unknown")
        if pipeline_filter and pipeline != pipeline_filter:
            continue
        groups.setdefault(pipeline, []).append(data)

    if not groups:
        print(f"No results found{f' for pipeline={pipeline_filter}' if pipeline_filter else ''}.")
        return

    for pipeline, runs in sorted(groups.items()):
        print(f"\nPipeline: {pipeline}")
        is_extraction = pipeline == "extraction"

        if is_extraction:
            header = f"{'Config':<40} {'schema':>6} {'compl':>6} {'faith':>6} {'clar':>6} {'COMP':>6} {'time/t':>7}"
        else:
            header = f"{'Config':<40} {'fields':>6} {'limits':>6} {'compl':>6} {'faith':>6} {'clar':>6} {'COMP':>6} {'time/t':>7}"
        print(header)
        print("-" * len(header))

        for run in runs:
            label = _model_label(run.get("config", {}))
            scores = run.get("scores", {})
            perf = run.get("performance", {})
            avg_time = perf.get("avg_time_per_trace_s", 0)

            if is_extraction:
                print(f"{label:<40} {scores.get('schema_ok', 0):>6.2f} "
                      f"{scores.get('completeness', 0):>6.2f} {scores.get('faithfulness', 0):>6.2f} "
                      f"{scores.get('clarity', 0):>6.2f} {scores.get('composite', 0):>6.2f} "
                      f"{avg_time:>6.1f}s")
            else:
                print(f"{label:<40} {scores.get('fields_present', 0):>6.2f} "
                      f"{scores.get('word_limits', 0):>6.2f} {scores.get('completeness', 0):>6.2f} "
                      f"{scores.get('faithfulness', 0):>6.2f} {scores.get('clarity', 0):>6.2f} "
                      f"{scores.get('composite', 0):>6.2f} {avg_time:>6.1f}s")

        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare eval results")
    parser.add_argument("--pipeline", choices=["extraction", "summarization"], help="Filter by pipeline")
    args = parser.parse_args()
    compare_results(args.pipeline)
