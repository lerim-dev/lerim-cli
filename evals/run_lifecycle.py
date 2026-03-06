"""Lifecycle eval runner — sequential syncs with periodic maintains.

Simulates realistic memory accumulation. All syncs are judge-scored.
Maintains run periodically to test dedup, merge, archive, consolidation.

Usage: PYTHONPATH=. python evals/run_lifecycle.py \
  --config evals/configs/eval_minimax_m25.toml \
  --traces-dir evals/dataset/traces/ --limit 20 --maintain-every 5
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

from evals.judge import invoke_judge
from evals.scores import EvalScore, compute_composite


EVALS_DIR = Path(__file__).parent
TRACES_DIR = EVALS_DIR / "traces"
RESULTS_DIR = EVALS_DIR / "results"
SYNC_JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "lifecycle_sync.md"
MAINTAIN_JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "lifecycle_maintain.md"


def _extract_session_time(trace_path: Path) -> float:
    """Parse first JSON line for a timestamp. Fall back to file mtime."""
    try:
        first_line = trace_path.open("r", encoding="utf-8").readline().strip()
        if first_line:
            data = json.loads(first_line)
            for key in ("timestamp", "created_at", "ts", "time"):
                val = data.get(key)
                if val is None:
                    continue
                if isinstance(val, (int, float)):
                    return float(val)
                if isinstance(val, str):
                    from datetime import datetime as _dt

                    for fmt in (
                        "%Y-%m-%dT%H:%M:%S.%fZ",
                        "%Y-%m-%dT%H:%M:%SZ",
                        "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%S",
                    ):
                        try:
                            return _dt.strptime(val, fmt).timestamp()
                        except ValueError:
                            continue
    except Exception:
        pass
    return trace_path.stat().st_mtime


def _count_memory_files(memory_root: Path) -> int:
    """Count .md files in decisions/ and learnings/ subdirs."""
    count = 0
    for subdir in ("decisions", "learnings"):
        d = memory_root / subdir
        if d.exists():
            count += sum(1 for f in d.iterdir() if f.suffix == ".md")
    return count


def _list_memory_titles(memory_root: Path) -> list[str]:
    """List titles from memory file frontmatter for judge context."""
    titles = []
    for subdir in ("decisions", "learnings"):
        d = memory_root / subdir
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix != ".md":
                continue
            try:
                text = f.read_text(encoding="utf-8")
                for line in text.splitlines():
                    if line.startswith("title:"):
                        titles.append(line.split(":", 1)[1].strip().strip('"'))
                        break
            except Exception:
                titles.append(f.stem)
    return titles


def _configure_from_eval(config: dict) -> tuple:
    """Build isolated eval config for lifecycle. Returns (Config, temp_dir)."""
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

    temp_dir = Path(tempfile.mkdtemp(prefix="lerim_eval_lifecycle_"))
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


def _read_json_safe(path: str | Path) -> dict | list | None:
    """Read a JSON file, return None on any error."""
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def _build_sync_judge_prompt(
    trace_path: Path,
    agent_trace_path: Path,
    memory_root: Path,
    run_folder: Path,
    memory_count: int,
) -> str:
    """Build judge prompt for a sync evaluation."""
    template = SYNC_JUDGE_PROMPT.read_text(encoding="utf-8")
    return template.format(
        trace_path=trace_path,
        agent_trace_path=agent_trace_path,
        memory_root=memory_root,
        run_folder=run_folder,
        memory_count=memory_count,
    )


def _build_maintain_judge_prompt(
    agent_trace_path: Path,
    memory_root: Path,
    run_folder: Path,
    before_count: int,
    after_count: int,
) -> str:
    """Build judge prompt for a maintain evaluation."""
    template = MAINTAIN_JUDGE_PROMPT.read_text(encoding="utf-8")
    return template.format(
        agent_trace_path=agent_trace_path,
        memory_root=memory_root,
        run_folder=run_folder,
        before_count=before_count,
        after_count=after_count,
    )


def _run_sync_eval(
    eval_cfg,
    trace_path: Path,
    memory_root: Path,
    workspace_root: Path,
    judge_agent: str,
    judge_timeout: int,
    trace_index: int,
    total_traces: int,
) -> dict:
    """Run one sync + judge and return score dict."""
    from lerim.runtime.agent import LerimAgent

    memory_before = _count_memory_files(memory_root)
    logger.info(
        "[{}/{}] Syncing: {} ({} memories)",
        trace_index,
        total_traces,
        trace_path.name,
        memory_before,
    )

    t0 = time.time()
    try:
        agent = LerimAgent(config=eval_cfg, default_cwd=str(Path.cwd()))
        result = agent.sync(
            trace_path,
            memory_root=str(memory_root),
            workspace_root=str(workspace_root),
        )
        wall_time = time.time() - t0
    except Exception as e:
        wall_time = time.time() - t0
        logger.warning(
            "[{}/{}] Sync error ({:.1f}s): {}", trace_index, total_traces, wall_time, e
        )
        return EvalScore(
            trace=trace_path.name,
            schema_ok=False,
            wall_time_s=round(wall_time, 2),
            judge_reasoning=str(e),
        ).__dict__

    # Collect artifacts
    artifacts = result.get("artifacts", {})
    counts = result.get("counts", {})
    run_folder = Path(result.get("run_folder", ""))
    agent_trace_path = run_folder / "agent_trace.json"
    extract_data = _read_json_safe(artifacts.get("extract", ""))
    schema_ok = extract_data is not None
    candidate_count = len(extract_data) if isinstance(extract_data, list) else 0

    # Judge scoring
    completeness = faithfulness = coherence = 0.0
    reasoning = ""
    try:
        logger.info("[{}/{}] Judging sync...", trace_index, total_traces)
        judge_start = time.time()
        prompt = _build_sync_judge_prompt(
            trace_path,
            agent_trace_path,
            memory_root,
            run_folder,
            memory_before,
        )
        judge_result = invoke_judge(judge_agent, prompt, timeout=judge_timeout)
        completeness = float(judge_result.get("completeness", 0))
        faithfulness = float(judge_result.get("faithfulness", 0))
        coherence = float(judge_result.get("coherence", 0))
        reasoning = judge_result.get("reasoning", "")
        logger.info("[{}/{}] Judge done ({:.1f}s)", trace_index, total_traces, time.time() - judge_start)
    except Exception as e:
        logger.warning("Judge error: {}", e)
        reasoning = f"Judge failed: {e}"

    composite = compute_composite(completeness, faithfulness, coherence)

    logger.success(
        "[{}/{}] add={} update={} no_op={} composite={:.2f} time={:.0f}s",
        trace_index,
        total_traces,
        counts.get("add", 0),
        counts.get("update", 0),
        counts.get("no_op", 0),
        composite,
        wall_time,
    )

    return {
        "trace": trace_path.name,
        "trace_index": trace_index,
        "memory_count_before": memory_before,
        "schema_ok": schema_ok,
        "candidate_count": candidate_count,
        "counts": counts,
        "completeness": completeness,
        "faithfulness": faithfulness,
        "coherence": coherence,
        "composite": composite,
        "wall_time_s": round(wall_time, 2),
        "judge_reasoning": reasoning,
    }


def _run_maintain_eval(
    eval_cfg,
    memory_root: Path,
    workspace_root: Path,
    judge_agent: str,
    judge_timeout: int,
    after_trace_index: int,
) -> dict:
    """Run one maintain + judge and return score dict."""
    from lerim.runtime.agent import LerimAgent

    memory_before = _count_memory_files(memory_root)
    logger.info(
        "[{}/...] Running maintain ({} memories)", after_trace_index, memory_before
    )

    t0 = time.time()
    try:
        agent = LerimAgent(config=eval_cfg, default_cwd=str(Path.cwd()))
        result = agent.maintain(
            memory_root=str(memory_root),
            workspace_root=str(workspace_root),
        )
        wall_time = time.time() - t0
    except Exception as e:
        wall_time = time.time() - t0
        logger.warning("Maintain error ({:.1f}s): {}", wall_time, e)
        return {
            "after_trace_index": after_trace_index,
            "memory_before": memory_before,
            "memory_after": _count_memory_files(memory_root),
            "counts": {},
            "completeness": 0.0,
            "faithfulness": 0.0,
            "coherence": 0.0,
            "composite": 0.0,
            "wall_time_s": round(wall_time, 2),
            "judge_reasoning": str(e),
        }

    counts = result.get("counts", {})
    memory_after = _count_memory_files(memory_root)
    run_folder = Path(result.get("run_folder", ""))
    agent_trace_path = run_folder / "agent_trace.json"

    # Judge scoring
    completeness = faithfulness = coherence = 0.0
    reasoning = ""
    try:
        logger.info("[{}/...] Judging maintain...", after_trace_index)
        judge_start = time.time()
        prompt = _build_maintain_judge_prompt(
            agent_trace_path,
            memory_root,
            run_folder,
            memory_before,
            memory_after,
        )
        judge_result = invoke_judge(judge_agent, prompt, timeout=judge_timeout)
        completeness = float(judge_result.get("completeness", 0))
        faithfulness = float(judge_result.get("faithfulness", 0))
        coherence = float(judge_result.get("coherence", 0))
        reasoning = judge_result.get("reasoning", "")
        logger.info("[{}/...] Judge done ({:.1f}s)", after_trace_index, time.time() - judge_start)
    except Exception as e:
        logger.warning("Judge error: {}", e)
        reasoning = f"Judge failed: {e}"

    composite = compute_composite(completeness, faithfulness, coherence)

    logger.success(
        "[{}/...] maintain: merged={} archived={} {}→{} composite={:.2f} time={:.0f}s",
        after_trace_index,
        counts.get("merged", 0),
        counts.get("archived", 0),
        memory_before,
        memory_after,
        composite,
        wall_time,
    )

    return {
        "after_trace_index": after_trace_index,
        "memory_before": memory_before,
        "memory_after": memory_after,
        "counts": counts,
        "completeness": completeness,
        "faithfulness": faithfulness,
        "coherence": coherence,
        "composite": composite,
        "wall_time_s": round(wall_time, 2),
        "judge_reasoning": reasoning,
    }


def run_lifecycle_eval(
    config_path: Path,
    traces_dir: Path | None = None,
    limit: int = 20,
    maintain_every: int = 5,
) -> dict:
    """Run lifecycle eval and return results dict."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    eval_cfg, temp_dir = _configure_from_eval(config)
    memory_root = temp_dir / "memory"
    workspace_root = temp_dir / "workspace"

    try:
        judge_agent = config.get("judge", {}).get("agent", "claude")
        judge_timeout = config.get("judge", {}).get("timeout_seconds", 300)
        effective_traces_dir = traces_dir or TRACES_DIR
        traces = sorted(effective_traces_dir.glob("*.jsonl")) + sorted(
            effective_traces_dir.glob("*.json")
        )
        traces = [t for t in traces if t.name != ".gitkeep"]

        # Sort chronologically
        traces.sort(key=_extract_session_time)

        total_available = len(traces)
        if limit and limit > 0:
            traces = traces[:limit]

        if not traces:
            logger.warning("No traces found. Add .jsonl or .json trace files.")
            return {}

        # Determine model label for logging
        first_section = config.get("extraction", config.get("lead", {}))
        model_label = (
            f"{first_section.get('model', '?')} ({first_section.get('provider', '?')})"
        )

        logger.info(
            "Lifecycle eval: {} traces, maintain every {}", len(traces), maintain_every
        )
        logger.info("Config: {}, judge: {}", model_label, judge_agent)
        logger.info(
            "Traces dir: {} ({} available, using {})",
            effective_traces_dir,
            total_available,
            len(traces),
        )

        sync_scores: list[dict] = []
        maintain_scores: list[dict] = []
        total_start = time.time()
        syncs_since_maintain = 0

        for i, trace_path in enumerate(traces, 1):
            # Run sync
            sync_result = _run_sync_eval(
                eval_cfg,
                trace_path,
                memory_root,
                workspace_root,
                judge_agent,
                judge_timeout,
                i,
                len(traces),
            )
            sync_scores.append(sync_result)
            syncs_since_maintain += 1

            # Run maintain every N syncs
            if maintain_every > 0 and i % maintain_every == 0:
                maintain_result = _run_maintain_eval(
                    eval_cfg,
                    memory_root,
                    workspace_root,
                    judge_agent,
                    judge_timeout,
                    i,
                )
                maintain_scores.append(maintain_result)
                syncs_since_maintain = 0

        # Final maintain if there were syncs since last maintain
        if syncs_since_maintain > 0 and maintain_every > 0:
            maintain_result = _run_maintain_eval(
                eval_cfg,
                memory_root,
                workspace_root,
                judge_agent,
                judge_timeout,
                len(traces),
            )
            maintain_scores.append(maintain_result)

        total_wall = time.time() - total_start

        # Aggregate scores
        sync_composites = [s.get("composite", 0) for s in sync_scores]
        maintain_composites = [m.get("composite", 0) for m in maintain_scores]
        sync_composite = (
            sum(sync_composites) / len(sync_composites) if sync_composites else 0
        )
        maintain_composite = (
            sum(maintain_composites) / len(maintain_composites)
            if maintain_composites
            else 0
        )

        # Overall: weighted by count
        total_evals = len(sync_scores) + len(maintain_scores)
        overall_composite = (
            (sum(sync_composites) + sum(maintain_composites)) / total_evals
            if total_evals > 0
            else 0
        )

        sync_times = [s.get("wall_time_s", 0) for s in sync_scores]
        maintain_times = [m.get("wall_time_s", 0) for m in maintain_scores]

        roles_cfg = {
            s: config.get(s, {})
            for s in ("lead", "explorer", "extraction", "summarization")
        }
        result = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pipeline": "lifecycle",
            "config": roles_cfg,
            "judge": {"agent": judge_agent},
            "performance": {
                "total_wall_time_s": round(total_wall, 2),
                "trace_count": len(traces),
                "sync_count": len(sync_scores),
                "maintain_count": len(maintain_scores),
                "maintain_every": maintain_every,
                "avg_sync_time_s": round(sum(sync_times) / len(sync_times), 2)
                if sync_times
                else 0,
                "avg_maintain_time_s": round(
                    sum(maintain_times) / len(maintain_times), 2
                )
                if maintain_times
                else 0,
            },
            "scores": {
                "sync_composite": round(sync_composite, 3),
                "maintain_composite": round(maintain_composite, 3),
                "overall_composite": round(overall_composite, 3),
            },
            "sync_scores": sync_scores,
            "maintain_scores": maintain_scores,
        }

        # Save results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"lifecycle_{ts}.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        logger.info("Results saved to: {}", out_path)

        # Print summary table
        print(f"\n{'=' * 70}")
        print(f"Lifecycle Eval Summary")
        print(f"{'=' * 70}")
        print(f"  Traces:     {len(traces)}")
        print(f"  Syncs:      {len(sync_scores)}")
        print(f"  Maintains:  {len(maintain_scores)}")
        print(f"  Total time: {total_wall:.0f}s")
        print()

        print(
            f"{'Trace':<35} {'Mem':>4} {'Add':>4} {'Upd':>4} {'Nop':>4} {'COMP':>6} {'Time':>6}"
        )
        print("-" * 70)
        for s in sync_scores:
            c = s.get("counts", {})
            print(
                f"{s.get('trace', '?'):<35} {s.get('memory_count_before', 0):>4} "
                f"{c.get('add', 0):>4} {c.get('update', 0):>4} {c.get('no_op', 0):>4} "
                f"{s.get('composite', 0):>6.2f} {s.get('wall_time_s', 0):>5.0f}s"
            )

        if maintain_scores:
            print()
            print(
                f"{'Maintain after':<20} {'Before':>6} {'After':>6} {'Mrgd':>5} {'Arch':>5} {'COMP':>6} {'Time':>6}"
            )
            print("-" * 60)
            for m in maintain_scores:
                c = m.get("counts", {})
                print(
                    f"trace {m.get('after_trace_index', '?'):<15} "
                    f"{m.get('memory_before', 0):>6} {m.get('memory_after', 0):>6} "
                    f"{c.get('merged', 0):>5} {c.get('archived', 0):>5} "
                    f"{m.get('composite', 0):>6.2f} {m.get('wall_time_s', 0):>5.0f}s"
                )

        print()
        print(f"  Sync composite:     {sync_composite:.3f}")
        print(f"  Maintain composite: {maintain_composite:.3f}")
        print(f"  Overall composite:  {overall_composite:.3f}")

        return result
    finally:
        from lerim.config.settings import set_config_override

        set_config_override(None)
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run lifecycle eval")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to eval config TOML (see evals/configs/ for examples)",
    )
    parser.add_argument(
        "--traces-dir",
        default=None,
        help="Override default traces directory (evals/traces/)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max traces to process (default: 20)",
    )
    parser.add_argument(
        "--maintain-every",
        type=int,
        default=5,
        help="Run maintain after every N syncs (default: 5)",
    )
    args = parser.parse_args()
    td = Path(args.traces_dir) if args.traces_dir else None
    run_lifecycle_eval(
        Path(args.config),
        traces_dir=td,
        limit=args.limit,
        maintain_every=args.maintain_every,
    )
