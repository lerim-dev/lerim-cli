"""Run agentic maintain eval.

Seeds a temporary memory_root by syncing all eval traces, then runs
maintain on the resulting memories. Judges quality of maintenance actions.

Usage: PYTHONPATH=. python evals/run_maintain.py --config evals/configs/eval_minimax_m25.toml [--traces-dir evals/dataset/traces/]
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

from evals.judge import build_judge_prompt, invoke_judge
from evals.scores import EvalScore, compute_composite


EVALS_DIR = Path(__file__).parent
TRACES_DIR = EVALS_DIR / "traces"
RESULTS_DIR = EVALS_DIR / "results"
JUDGE_PROMPT = EVALS_DIR / "judge_prompts" / "maintain.md"


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


def _seed_memories(
    memory_root: Path,
    workspace_root: Path,
    limit: int = 0,
    traces_dir: Path | None = None,
) -> int:
    """Sync eval traces to populate memory_root. Return count of synced traces."""
    from lerim.runtime.agent import LerimAgent

    effective_traces_dir = traces_dir or TRACES_DIR
    traces = sorted(effective_traces_dir.glob("*.jsonl")) + sorted(
        effective_traces_dir.glob("*.json")
    )
    traces = [t for t in traces if t.name != ".gitkeep"]
    if limit and limit > 0:
        traces = traces[:limit]
    synced = 0
    for trace_path in traces:
        print(f"    Seeding from: {trace_path.name}")
        try:
            agent = LerimAgent(default_cwd=str(Path.cwd()))
            agent.sync(
                trace_path,
                memory_root=str(memory_root),
                workspace_root=str(workspace_root),
            )
            synced += 1
        except Exception as e:
            print(f"      Seed sync failed: {e}")
    return synced


def _list_memory_files(memory_root: Path) -> list[str]:
    """List all .md memory files under memory_root."""
    return [str(p) for p in sorted(memory_root.rglob("*.md"))]


def run_maintain_eval(
    config_path: Path, limit: int = 0, traces_dir: Path | None = None
) -> dict:
    """Run maintain eval and return results dict. limit=0 means all traces."""
    with config_path.open("rb") as f:
        config = tomllib.load(f)

    _configure_from_eval(config)

    from lerim.runtime.agent import LerimAgent

    judge_agent = config.get("judge", {}).get("agent", "claude")
    judge_timeout = config.get("judge", {}).get("timeout_seconds", 300)

    with tempfile.TemporaryDirectory(prefix="lerim_maintain_eval_") as tmpdir:
        memory_root = Path(tmpdir) / "memory"
        workspace_root = Path(tmpdir) / "workspace"
        memory_root.mkdir()
        workspace_root.mkdir()
        (memory_root / "decisions").mkdir()
        (memory_root / "learnings").mkdir()
        (memory_root / "summaries").mkdir()
        (memory_root / "archived" / "decisions").mkdir(parents=True)
        (memory_root / "archived" / "learnings").mkdir(parents=True)

        # Phase 1: Seed memories via sync
        print("Phase 1: Seeding memories via sync...")
        seed_t0 = time.time()
        seeded = _seed_memories(
            memory_root, workspace_root, limit=limit, traces_dir=traces_dir
        )
        seed_time = time.time() - seed_t0

        memory_files_before = _list_memory_files(memory_root)
        print(
            f"  Seeded {seeded} traces, {len(memory_files_before)} memory files ({seed_time:.1f}s)"
        )

        if not memory_files_before:
            print("  No memories to maintain — seeding produced no files.")
            return {"error": "no_memories_seeded"}

        # Phase 2: Run maintain
        print("\nPhase 2: Running maintain...")
        t0 = time.time()
        try:
            agent = LerimAgent(default_cwd=str(Path.cwd()))
            result = agent.maintain(
                memory_root=str(memory_root),
                workspace_root=str(workspace_root),
            )
            wall_time = time.time() - t0
            success = True

            artifacts = result.get("artifacts", {})
            actions_data = _read_json_safe(artifacts.get("maintain_actions", ""))
            counts = result.get("counts", {})

        except Exception as e:
            wall_time = time.time() - t0
            success = False
            actions_data = None
            counts = {}
            print(f"  Maintain error ({wall_time:.1f}s): {e}")

        memory_files_after = _list_memory_files(memory_root)

        # Judge
        judge_payload = json.dumps(
            {
                "memory_files_before_count": len(memory_files_before),
                "memory_files_after_count": len(memory_files_after),
                "maintain_actions": actions_data,
                "counts": counts,
                "success": success,
            },
            indent=2,
            ensure_ascii=False,
        )

        completeness = faithfulness = coherence = 0.0
        reasoning = ""
        if success:
            try:
                # For maintain, we don't have a trace_path — use a placeholder
                prompt = build_judge_prompt(
                    JUDGE_PROMPT,
                    Path("(maintain-eval-no-trace)"),
                    judge_payload,
                )
                judge_result = invoke_judge(judge_agent, prompt, timeout=judge_timeout)
                completeness = float(judge_result.get("completeness", 0))
                faithfulness = float(judge_result.get("faithfulness", 0))
                coherence = float(judge_result.get("coherence", 0))
                reasoning = judge_result.get("reasoning", "")
            except Exception as e:
                print(f"  Judge error: {e}")
                reasoning = f"Judge failed: {e}"

        composite = compute_composite(completeness, faithfulness, coherence)

    total_wall = seed_time + wall_time

    roles_cfg = {
        s: config.get(s, {})
        for s in ("lead", "explorer", "extraction", "summarization")
    }
    result_doc = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": "maintain",
        "config": roles_cfg,
        "judge": {"agent": judge_agent},
        "performance": {
            "seed_time_s": round(seed_time, 2),
            "maintain_time_s": round(wall_time, 2),
            "total_wall_time_s": round(total_wall, 2),
            "seeded_traces": seeded,
            "memory_files_before": len(memory_files_before),
            "memory_files_after": len(memory_files_after),
        },
        "scores": {
            "completeness": completeness,
            "faithfulness": faithfulness,
            "coherence": coherence,
            "composite": round(composite, 3),
            "success": success,
        },
        "counts": counts,
        "judge_reasoning": reasoning,
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"maintain_{ts}.json"
    out_path.write_text(json.dumps(result_doc, indent=2, ensure_ascii=False) + "\n")
    print(f"\nResults saved to: {out_path}")

    print(f"\n--- Maintain Eval ---")
    print(f"  Success:     {success}")
    print(f"  Seed time:   {seed_time:.1f}s ({seeded} traces)")
    print(f"  Maintain:    {wall_time:.1f}s")
    print(f"  Memories:    {len(memory_files_before)} -> {len(memory_files_after)}")
    print(f"  Counts:      {counts}")
    print(f"  Composite:   {composite:.2f}")
    print(f"  Completeness: {completeness:.2f}")
    print(f"  Faithfulness: {faithfulness:.2f}")
    print(f"  Coherence:   {coherence:.2f}")

    return result_doc


def _read_json_safe(path: str) -> dict | list | None:
    """Read a JSON file, return None on any error."""
    if not path:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run agentic maintain eval")
    parser.add_argument(
        "--config",
        required=True,
        help="Path to eval config TOML (see evals/configs/ for examples)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Max traces to seed (0=all)"
    )
    parser.add_argument(
        "--traces-dir",
        default=None,
        help="Override default traces directory (evals/traces/)",
    )
    args = parser.parse_args()
    td = Path(args.traces_dir) if args.traces_dir else None
    run_maintain_eval(Path(args.config), limit=args.limit, traces_dir=td)
