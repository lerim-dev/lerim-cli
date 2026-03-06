"""Build a diverse eval dataset from real coding-agent session traces.

Scans connected platforms for session files, uses a coding agent CLI to
assess quality and label topics, selects diverse traces, and exports them
for use with the eval runners.

Usage:
    PYTHONPATH=. python evals/dataset/build.py --agent claude
    PYTHONPATH=. python evals/dataset/build.py --agent claude --config evals/dataset/config.toml --count 50
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DATASET_DIR = Path(__file__).parent
DEFAULT_CONFIG = DATASET_DIR / "config.toml"


# ---------------------------------------------------------------------------
# Platform parsers — lightweight JSONL scanning per platform
# ---------------------------------------------------------------------------

def _parse_claude_session(path: Path) -> dict[str, Any] | None:
    """Parse a Claude Code JSONL session for catalog metadata."""
    messages = 0
    user_texts: list[str] = []
    project = ""
    try:
        # Derive project from directory path
        parts = path.parts
        for i, p in enumerate(parts):
            if p == "projects" and i + 1 < len(parts):
                project = parts[i + 1].replace("-Users-", "").split("-")[-1]
                break

        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entry_type = entry.get("type", "")
                if entry_type in ("user", "assistant"):
                    messages += 1
                if entry_type == "user" and len(user_texts) < 5:
                    msg = entry.get("message", {})
                    content = msg.get("content", "") if isinstance(msg, dict) else ""
                    if isinstance(content, str) and content.strip():
                        user_texts.append(content[:500])
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                if text.strip():
                                    user_texts.append(text[:500])
                                    break
    except (OSError, UnicodeDecodeError):
        return None

    if messages == 0:
        return None
    return {
        "message_count": messages,
        "first_user_messages": user_texts,
        "project": project,
    }


def _parse_codex_session(path: Path) -> dict[str, Any] | None:
    """Parse a Codex CLI JSONL session for catalog metadata."""
    messages = 0
    user_texts: list[str] = []
    project = ""
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = entry.get("type", "")
                payload = entry.get("payload", {})
                if not isinstance(payload, dict):
                    continue

                if etype == "session_meta" and not project:
                    project = Path(payload.get("cwd", "")).name

                if etype == "event_msg":
                    ptype = payload.get("type", "")
                    if ptype in ("user_message", "agent_message"):
                        messages += 1
                    if ptype == "user_message" and len(user_texts) < 5:
                        text = payload.get("message", "")
                        if isinstance(text, str) and text.strip():
                            user_texts.append(text[:500])
    except (OSError, UnicodeDecodeError):
        return None

    if messages == 0:
        return None
    return {
        "message_count": messages,
        "first_user_messages": user_texts,
        "project": project,
    }


def _parse_opencode_session(path: Path) -> dict[str, Any] | None:
    """Parse an OpenCode cached JSONL session for catalog metadata."""
    messages = 0
    user_texts: list[str] = []
    project = ""
    try:
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # First line is session metadata
                if i == 0:
                    project = Path(entry.get("cwd", "")).name
                    continue

                role = entry.get("role", "")
                if role in ("user", "assistant"):
                    messages += 1
                if role == "user" and len(user_texts) < 5:
                    text = entry.get("content", "")
                    if isinstance(text, str) and text.strip():
                        user_texts.append(text[:500])
    except (OSError, UnicodeDecodeError):
        return None

    if messages == 0:
        return None
    return {
        "message_count": messages,
        "first_user_messages": user_texts,
        "project": project,
    }


def _parse_cursor_session(path: Path) -> dict[str, Any] | None:
    """Parse a Cursor cached JSONL session for catalog metadata."""
    messages = 0
    user_texts: list[str] = []
    try:
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # First line is composer metadata, skip it
                if i == 0 and "composerId" in entry:
                    continue

                btype = entry.get("type")
                if btype in (1, 2):
                    messages += 1
                if btype == 1 and len(user_texts) < 5:
                    text = entry.get("text", "")
                    if isinstance(text, dict):
                        text = text.get("text", "")
                    if isinstance(text, str) and text.strip():
                        user_texts.append(text[:500])
    except (OSError, UnicodeDecodeError):
        return None

    if messages == 0:
        return None
    return {
        "message_count": messages,
        "first_user_messages": user_texts,
        "project": "",
    }


_PARSERS: dict[str, Any] = {
    "claude": _parse_claude_session,
    "codex": _parse_codex_session,
    "opencode": _parse_opencode_session,
    "cursor": _parse_cursor_session,
}


# ---------------------------------------------------------------------------
# Phase 1: Scan — discover and parse all session files
# ---------------------------------------------------------------------------

def phase_scan(sources: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Scan configured platform sources and return candidate metadata list."""
    from lerim.adapters.registry import KNOWN_PLATFORMS

    candidates: list[dict[str, Any]] = []

    for source in sources:
        platform = source["platform"]
        source_path = Path(source["path"]).expanduser().resolve()

        # Validate platform is supported
        if platform not in KNOWN_PLATFORMS:
            print(
                f"  ERROR: Unsupported platform '{platform}'. "
                f"Supported: {', '.join(KNOWN_PLATFORMS)}",
                file=sys.stderr,
            )
            sys.exit(1)

        # Check path exists
        if not source_path.exists():
            print(f"  WARN: Path not found for {platform}: {source_path}. Skipping.")
            continue

        # Glob for JSONL files
        jsonl_files = sorted(source_path.rglob("*.jsonl"))
        if not jsonl_files:
            print(f"  WARN: No session files found for {platform} at {source_path}. Skipping.")
            continue

        parser = _PARSERS.get(platform)
        if not parser:
            print(f"  WARN: No parser for platform '{platform}'. Skipping.")
            continue

        print(f"  Scanning {platform}: {len(jsonl_files)} files at {source_path}")
        for fpath in jsonl_files:
            meta = parser(fpath)
            if meta is None:
                continue
            candidates.append({
                "path": str(fpath),
                "platform": platform,
                "project": meta["project"],
                "file_size_bytes": fpath.stat().st_size,
                "message_count": meta["message_count"],
                "first_user_messages": meta["first_user_messages"],
            })

    if not candidates:
        print("  ERROR: No session files found for any configured platform.", file=sys.stderr)
        sys.exit(1)

    print(f"  Total candidates: {len(candidates)}")
    return candidates


# ---------------------------------------------------------------------------
# Phase 2: Assess — LLM-based topic labeling and quality scoring
# ---------------------------------------------------------------------------

_ASSESS_PROMPT_TEMPLATE = """\
You are assessing coding-agent session traces for an eval benchmark dataset.

For each session below, analyze the first user messages and produce a JSON assessment.

Return a JSON array with one object per session, in the same order as the input.
Each object must have exactly these fields:
- "index": the session index number (integer, as provided)
- "topic": a short label (3-8 words) describing the session's main task (e.g., "debugging auth flow", "adding unit tests", "refactoring CLI args")
- "session_type": one of: "debugging", "feature_implementation", "refactoring", "code_review", "config_infra", "testing", "architecture", "other"
- "quality_score": integer 1-5 rating based on how valuable this session is for eval benchmarking:
  - 5: Rich session with architectural decisions, complex problem-solving, multi-step implementation
  - 4: Substantial coding task with clear decisions or learnings
  - 3: Decent coding task, some useful content
  - 2: Simple/mechanical task, minimal learning value
  - 1: Trivial, empty, or non-coding session
- "quality_notes": brief (1 sentence) rationale for the quality score

Return ONLY the JSON array, no other text.

Sessions to assess:
{sessions}
"""


def _invoke_agent(agent: str, prompt: str, timeout: int = 180) -> str:
    """Invoke a coding agent CLI and return raw stdout."""
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", ""]
    elif agent == "codex":
        cmd = ["codex", "exec", prompt, "--json", "--ephemeral"]
    elif agent == "opencode":
        cmd = ["opencode", "run", prompt, "--format", "json"]
    else:
        raise ValueError(f"Unknown agent: {agent}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Agent {agent} failed: {result.stderr[:500]}")
    return result.stdout


def _parse_json_from_output(agent: str, raw: str) -> Any:
    """Extract JSON from agent CLI output."""
    # For claude --output-format json, unwrap the result field
    if agent == "claude":
        try:
            wrapper = json.loads(raw)
            if isinstance(wrapper, dict) and "result" in wrapper:
                text = wrapper["result"]
            else:
                text = raw
        except (json.JSONDecodeError, TypeError):
            text = raw
    else:
        text = raw

    # Try direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    raise RuntimeError(f"Could not parse JSON from agent output: {text[:300]}")


def phase_assess(
    candidates: list[dict[str, Any]],
    agent: str,
    batch_size: int = 8,
) -> list[dict[str, Any]]:
    """Assess candidates via LLM agent for topic, type, and quality."""
    total = len(candidates)
    assessed = 0

    for batch_start in range(0, total, batch_size):
        batch = candidates[batch_start : batch_start + batch_size]
        batch_end = min(batch_start + batch_size, total)
        print(f"  Assessing batch {batch_start + 1}-{batch_end} of {total}...")

        # Build session descriptions for the prompt
        session_descs = []
        for i, cand in enumerate(batch):
            idx = batch_start + i
            msgs = cand.get("first_user_messages", [])
            msgs_text = "\n    ".join(f"- {m[:300]}" for m in msgs[:3]) if msgs else "(no user messages)"
            session_descs.append(
                f"Session {idx}:\n"
                f"  Platform: {cand['platform']}\n"
                f"  Messages: {cand['message_count']}\n"
                f"  Project: {cand.get('project', 'unknown')}\n"
                f"  First user messages:\n    {msgs_text}"
            )

        prompt = _ASSESS_PROMPT_TEMPLATE.format(sessions="\n\n".join(session_descs))

        try:
            raw = _invoke_agent(agent, prompt, timeout=300)
            assessments = _parse_json_from_output(agent, raw)
            if not isinstance(assessments, list):
                print(f"    WARN: Expected JSON array, got {type(assessments).__name__}. Skipping batch.")
                continue

            # Merge assessments back into candidates
            for assessment in assessments:
                if not isinstance(assessment, dict):
                    continue
                idx = assessment.get("index")
                if idx is None or not isinstance(idx, int) or idx < 0 or idx >= total:
                    continue
                candidates[idx]["topic"] = assessment.get("topic", "unknown")
                candidates[idx]["session_type"] = assessment.get("session_type", "other")
                candidates[idx]["quality_score"] = int(assessment.get("quality_score", 1))
                candidates[idx]["quality_notes"] = assessment.get("quality_notes", "")
                assessed += 1

        except Exception as e:
            print(f"    WARN: Batch assessment failed: {e}")
            # Fill defaults for this batch
            for i in range(batch_start, batch_end):
                candidates[i].setdefault("topic", "unknown")
                candidates[i].setdefault("session_type", "other")
                candidates[i].setdefault("quality_score", 1)
                candidates[i].setdefault("quality_notes", "assessment failed")

    # Fill defaults for any unassessed candidates
    for cand in candidates:
        cand.setdefault("topic", "unknown")
        cand.setdefault("session_type", "other")
        cand.setdefault("quality_score", 1)
        cand.setdefault("quality_notes", "not assessed")

    print(f"  Assessed {assessed}/{total} candidates")
    return candidates


# ---------------------------------------------------------------------------
# Phase 3: Select — diversity-optimized greedy selection
# ---------------------------------------------------------------------------

def _length_category(msg_count: int) -> str:
    """Classify session length."""
    if msg_count < 20:
        return "short"
    elif msg_count <= 80:
        return "medium"
    return "long"


def phase_select(
    candidates: list[dict[str, Any]],
    trace_count: int,
    diversity: dict[str, Any],
    quality: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select diverse traces from assessed candidates."""
    min_messages = quality.get("min_messages", 5)
    min_quality = quality.get("min_quality_score", 3)
    max_share = diversity.get("max_platform_share", 0.6)

    # Hard filter
    filtered = [
        c for c in candidates
        if c["message_count"] >= min_messages and c.get("quality_score", 0) >= min_quality
    ]
    print(f"  After quality filter: {len(filtered)} candidates (from {len(candidates)})")

    if not filtered:
        print("  WARN: No candidates pass quality filters. Relaxing min_quality_score to 1.")
        filtered = [c for c in candidates if c["message_count"] >= min_messages]

    if not filtered:
        print("  ERROR: No candidates with enough messages.", file=sys.stderr)
        return []

    # Sort by quality (descending), then message count for tiebreaking
    filtered.sort(key=lambda c: (-c.get("quality_score", 0), -c["message_count"]))

    # Determine platform counts for balance
    platforms = set(c["platform"] for c in filtered)
    num_platforms = len(platforms)
    max_per_platform = (
        int(trace_count * max_share) + 1 if num_platforms > 1 else trace_count
    )

    # Target length distribution
    target_short = int(trace_count * diversity.get("short_pct", 0.2))
    target_medium = int(trace_count * diversity.get("medium_pct", 0.5))
    target_long = trace_count - target_short - target_medium

    selected: list[dict[str, Any]] = []
    platform_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    length_counts: dict[str, int] = {"short": 0, "medium": 0, "long": 0}
    used_paths: set[str] = set()

    def _can_add(cand: dict[str, Any]) -> bool:
        """Check if adding this candidate respects diversity constraints."""
        p = cand["platform"]
        if platform_counts.get(p, 0) >= max_per_platform:
            return False
        return True

    def _diversity_score(cand: dict[str, Any]) -> float:
        """Score how much this candidate improves diversity (higher = better)."""
        score = 0.0
        p = cand["platform"]
        stype = cand.get("session_type", "other")
        lcat = _length_category(cand["message_count"])

        # Prefer underrepresented platforms
        if platform_counts.get(p, 0) == 0:
            score += 3.0
        elif num_platforms > 1:
            avg = len(selected) / num_platforms if num_platforms > 0 else 0
            if platform_counts.get(p, 0) < avg:
                score += 1.0

        # Prefer new session types
        if type_counts.get(stype, 0) == 0:
            score += 2.0

        # Prefer underrepresented length categories
        targets = {"short": target_short, "medium": target_medium, "long": target_long}
        if length_counts[lcat] < targets.get(lcat, 0):
            score += 1.5

        # Prefer different projects
        projects_seen = set(s.get("project", "") for s in selected)
        if cand.get("project", "") and cand["project"] not in projects_seen:
            score += 1.0

        return score

    # Greedy selection: iterate through candidates, prioritize diversity
    # First pass: pick candidates that improve diversity
    remaining = list(filtered)
    while len(selected) < trace_count and remaining:
        # Score all remaining candidates
        scored = [
            (c, _diversity_score(c) + c.get("quality_score", 0) * 0.5)
            for c in remaining
            if c["path"] not in used_paths and _can_add(c)
        ]
        if not scored:
            break

        # Pick the best
        scored.sort(key=lambda x: -x[1])
        best = scored[0][0]

        selected.append(best)
        used_paths.add(best["path"])
        platform_counts[best["platform"]] = platform_counts.get(best["platform"], 0) + 1
        stype = best.get("session_type", "other")
        type_counts[stype] = type_counts.get(stype, 0) + 1
        lcat = _length_category(best["message_count"])
        length_counts[lcat] += 1
        remaining.remove(best)

    # Assign IDs and length categories
    platform_idx: dict[str, int] = {}
    for trace in selected:
        p = trace["platform"]
        platform_idx[p] = platform_idx.get(p, 0) + 1
        trace["id"] = f"{p}_{platform_idx[p]:03d}"
        trace["file"] = f"traces/{trace['id']}.jsonl"
        trace["length_category"] = _length_category(trace["message_count"])

    # Report
    print(f"  Selected {len(selected)} traces:")
    print(f"    Platforms: {dict(platform_counts)}")
    print(f"    Session types: {dict(type_counts)}")
    print(f"    Length distribution: {dict(length_counts)}")
    projects = set(s.get("project", "unknown") for s in selected)
    print(f"    Projects: {projects}")

    return selected


# ---------------------------------------------------------------------------
# Phase 4: Export — copy traces and write manifest
# ---------------------------------------------------------------------------

def phase_export(
    selected: list[dict[str, Any]],
    output_dir: Path,
    agent_used: str,
) -> None:
    """Copy selected traces to output dir and write manifest + catalog."""
    traces_dir = output_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    manifest_traces = []
    for trace in selected:
        src = Path(trace["path"])
        dst = traces_dir / f"{trace['id']}.jsonl"

        shutil.copy2(src, dst)

        manifest_traces.append({
            "id": trace["id"],
            "file": trace["file"],
            "platform": trace["platform"],
            "project": trace.get("project", ""),
            "session_type": trace.get("session_type", "other"),
            "message_count": trace["message_count"],
            "length_category": trace["length_category"],
            "topic": trace.get("topic", "unknown"),
            "quality_score": trace.get("quality_score", 0),
            "source_path": trace["path"],
        })

    manifest = {
        "version": 1,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "description": f"{len(selected)} real session traces for Lerim eval benchmark",
        "agent_used": agent_used,
        "trace_count": len(selected),
        "diversity_report": {
            "platforms": {},
            "session_types": {},
            "length_categories": {"short": 0, "medium": 0, "long": 0},
            "projects": [],
        },
        "traces": manifest_traces,
    }

    # Build diversity report
    for t in manifest_traces:
        p = t["platform"]
        manifest["diversity_report"]["platforms"][p] = (
            manifest["diversity_report"]["platforms"].get(p, 0) + 1
        )
        st = t["session_type"]
        manifest["diversity_report"]["session_types"][st] = (
            manifest["diversity_report"]["session_types"].get(st, 0) + 1
        )
        manifest["diversity_report"]["length_categories"][t["length_category"]] += 1

    manifest["diversity_report"]["projects"] = sorted(
        set(t.get("project", "") for t in manifest_traces if t.get("project"))
    )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  Manifest written: {manifest_path}")
    print(f"  Traces exported: {traces_dir} ({len(selected)} files)")


def write_catalog(
    candidates: list[dict[str, Any]],
    output_dir: Path,
    agent_used: str,
) -> None:
    """Write catalog.json with all candidate metadata."""
    # Remove first_user_messages from catalog (too large for output)
    catalog_entries = []
    for c in candidates:
        entry = {k: v for k, v in c.items() if k != "first_user_messages"}
        catalog_entries.append(entry)

    catalog = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent_used": agent_used,
        "candidate_count": len(catalog_entries),
        "candidates": catalog_entries,
    }

    catalog_path = output_dir / "catalog.json"
    catalog_path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"  Catalog written: {catalog_path} ({len(catalog_entries)} candidates)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the dataset creation pipeline."""
    parser = argparse.ArgumentParser(
        description="Build eval dataset from real coding-agent session traces"
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Coding agent CLI to use for LLM tasks (claude, codex, opencode)",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to dataset config TOML (default: evals/dataset/config.toml)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override trace_count from config",
    )
    args = parser.parse_args()

    # Validate agent
    supported_agents = ("claude", "codex", "opencode")
    if args.agent not in supported_agents:
        print(
            f"ERROR: Unknown agent '{args.agent}'. Supported: {', '.join(supported_agents)}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load config
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"ERROR: Config not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    with config_path.open("rb") as f:
        config = tomllib.load(f)

    sources = config.get("sources", [])
    if not sources:
        print("ERROR: No [[sources]] configured in config.", file=sys.stderr)
        sys.exit(1)

    diversity = config.get("diversity", {})
    quality = config.get("quality", {})
    trace_count = args.count or config.get("dataset", {}).get("trace_count", 50)
    agent = args.agent

    print(f"=== Eval Dataset Pipeline ===")
    print(f"Agent: {agent}")
    print(f"Config: {config_path}")
    print(f"Target traces: {trace_count}")
    print()

    # Phase 1: Scan
    print("Phase 1: Scanning session files...")
    candidates = phase_scan(sources)
    print()

    # Phase 2: Assess
    print("Phase 2: Assessing quality via agent...")
    candidates = phase_assess(candidates, agent)
    write_catalog(candidates, DATASET_DIR, agent)
    print()

    # Phase 3: Select
    print("Phase 3: Selecting diverse traces...")
    selected = phase_select(candidates, trace_count, diversity, quality)
    if not selected:
        print("ERROR: No traces selected.", file=sys.stderr)
        sys.exit(1)
    print()

    # Phase 4: Export
    print("Phase 4: Exporting traces...")
    phase_export(selected, DATASET_DIR, agent)
    print()

    print(f"=== Done: {len(selected)} traces exported to {DATASET_DIR / 'traces'} ===")
    print(f"Run evals with: PYTHONPATH=. python evals/run_extraction.py --traces-dir evals/dataset/traces/")


if __name__ == "__main__":
    main()
