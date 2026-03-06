"""Run agentic sync eval across all traces in evals/traces/.

Instantiates a LerimAgent, calls agent.sync() on each trace with an
isolated temporary memory_root, invokes an LLM judge, and saves results.

Usage: PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_minimax_m25.toml [--traces-dir evals/dataset/traces/]
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from evals.judge import build_judge_prompt, invoke_judge
from evals.scores import EvalScore, compute_composite


EVALS_DIR = Path(__file__).parent
TRACES_DIR = EVALS_DIR / "traces"
RESULTS_DIR = EVALS_DIR / "results"
JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "sync.md"


def _configure_from_eval(config: dict) -> None:
    """Override lerim config for all roles from eval TOML.

    Reads [lead], [explorer], [extraction], [summarization] sections
    independently.  All four are required.
    """
    from lerim.config.settings import reload_config, save_config_patch

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
    roles_patch: dict = {}
    for section_name, role_name in section_to_role.items():
        section = config[section_name]
        role_patch: dict = {
            "provider": section.get("provider", "openrouter"),
            "model": section.get("model", "qwen/qwen3-coder-30b-a3b-instruct"),
            "thinking": section.get("thinking", True),
        }
        if "timeout_seconds" in section:
            role_patch["timeout_seconds"] = int(section["timeout_seconds"])
        roles_patch[role_name] = role_patch

    save_config_patch({"roles": roles_patch})
    reload_config()


def run_sync_eval(
    config_path: Path, limit: int = 0, traces_dir: Path | None = None
) -> dict:
    """Run sync eval on traces and return results dict. limit=0 means all."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    _configure_from_eval(config)

    from lerim.runtime.agent import LerimAgent

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
        print("No traces found in evals/traces/.")
        return {}

    per_trace: list[dict] = []
    total_start = time.time()
    repo_root = Path.cwd()

    for trace_path in traces:
        print(f"  Syncing: {trace_path.name}")
        t0 = time.time()

        # Each trace gets an isolated memory_root + workspace
        with tempfile.TemporaryDirectory(prefix="lerim_sync_eval_") as tmpdir:
            memory_root = Path(tmpdir) / "memory"
            workspace_root = Path(tmpdir) / "workspace"
            memory_root.mkdir()
            workspace_root.mkdir()
            # Create required memory subdirs
            (memory_root / "decisions").mkdir()
            (memory_root / "learnings").mkdir()
            (memory_root / "summaries").mkdir()

            try:
                agent = LerimAgent(default_cwd=str(repo_root))
                result = agent.sync(
                    trace_path,
                    memory_root=str(memory_root),
                    workspace_root=str(workspace_root),
                )
                wall_time = time.time() - t0
                success = True

                # Collect artifacts for judging
                artifacts = result.get("artifacts", {})
                extract_data = _read_json_safe(artifacts.get("extract", ""))
                summary_data = _read_json_safe(artifacts.get("summary", ""))
                actions_data = _read_json_safe(artifacts.get("memory_actions", ""))
                counts = result.get("counts", {})

            except Exception as e:
                wall_time = time.time() - t0
                success = False
                extract_data = None
                summary_data = None
                actions_data = None
                counts = {}
                print(f"    Sync error ({wall_time:.1f}s): {e}")
                per_trace.append(
                    EvalScore(
                        trace=trace_path.name,
                        schema_ok=False,
                        wall_time_s=round(wall_time, 2),
                        judge_reasoning=str(e),
                    ).__dict__
                )
                continue

        # Deterministic checks
        schema_ok = (
            extract_data is not None
            and summary_data is not None
            and actions_data is not None
        )
        has_candidates = isinstance(extract_data, list) and len(extract_data) > 0

        # Build combined output for judge
        judge_payload = json.dumps(
            {
                "extraction": extract_data,
                "summary": summary_data,
                "memory_actions": actions_data,
                "counts": counts,
            },
            indent=2,
            ensure_ascii=False,
        )

        # Judge scoring
        try:
            prompt = build_judge_prompt(JUDGE_PROMPT, trace_path, judge_payload)
            judge_result = invoke_judge(judge_agent, prompt, timeout=judge_timeout)
            completeness = float(judge_result.get("completeness", 0))
            faithfulness = float(judge_result.get("faithfulness", 0))
            coherence = float(judge_result.get("coherence", 0))
            reasoning = judge_result.get("reasoning", "")
        except Exception as e:
            print(f"    Judge error: {e}")
            completeness = faithfulness = coherence = 0.0
            reasoning = f"Judge failed: {e}"

        composite = compute_composite(completeness, faithfulness, coherence)

        score = EvalScore(
            trace=trace_path.name,
            schema_ok=schema_ok,
            has_candidates=has_candidates,
            completeness=completeness,
            faithfulness=faithfulness,
            clarity=coherence,  # reuse clarity field for coherence
            composite=composite,
            wall_time_s=round(wall_time, 2),
            judge_reasoning=reasoning,
            candidate_count=len(extract_data) if isinstance(extract_data, list) else 0,
        )
        per_trace.append(score.__dict__)
        print(
            f"    ok={schema_ok} candidates={score.candidate_count} "
            f"counts={counts} composite={composite:.2f} time={wall_time:.1f}s"
        )

    total_wall = time.time() - total_start

    # Aggregate
    n = len(per_trace) or 1
    agg = {
        k: round(sum(t.get(k, 0) for t in per_trace) / n, 3)
        for k in ("completeness", "faithfulness", "clarity", "composite", "wall_time_s")
    }
    agg["schema_ok"] = round(sum(1 for t in per_trace if t.get("schema_ok")) / n, 3)

    roles_cfg = {
        s: config.get(s, {})
        for s in ("lead", "explorer", "extraction", "summarization")
    }
    result = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": "sync",
        "config": roles_cfg,
        "judge": {"agent": judge_agent},
        "performance": {
            "total_wall_time_s": round(total_wall, 2),
            "avg_time_per_trace_s": round(total_wall / len(traces), 2) if traces else 0,
            "trace_count": len(traces),
        },
        "scores": agg,
        "per_trace": per_trace,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"sync_{ts}.json"
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"\nResults saved to: {out_path}")

    # Print summary table
    print(
        f"\n{'Trace':<40} {'OK':>4} {'Cands':>5} {'Compl':>6} {'Faith':>6} {'Coher':>6} {'COMP':>6} {'Time':>7}"
    )
    print("-" * 90)
    for t in per_trace:
        print(
            f"{t['trace']:<40} {'yes' if t['schema_ok'] else 'FAIL':>4} "
            f"{t.get('candidate_count', 0):>5} "
            f"{t['completeness']:>6.2f} {t['faithfulness']:>6.2f} {t['clarity']:>6.2f} "
            f"{t['composite']:>6.2f} {t['wall_time_s']:>6.1f}s"
        )
    print("-" * 90)
    print(
        f"{'AVERAGE':<40} {agg['schema_ok']:>4.0%} {'':>5} "
        f"{agg['completeness']:>6.2f} {agg['faithfulness']:>6.2f} {agg['clarity']:>6.2f} "
        f"{agg['composite']:>6.2f} {agg['wall_time_s']:>6.1f}s"
    )

    return result


def _read_json_safe(path: str) -> dict | list | None:
    """Read a JSON file, return None on any error."""
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run agentic sync eval")
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
    run_sync_eval(Path(args.config), limit=args.limit, traces_dir=td)
