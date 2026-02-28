"""Thin wrapper that invokes coding agent CLIs as judge.

Supports claude, codex, and opencode as judge agents. Reads prompt templates
from judge_prompts/ and injects trace path + pipeline output.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


def invoke_judge(agent: str, prompt: str, timeout: int = 120) -> dict:
    """Invoke a coding agent CLI as judge, return parsed JSON."""
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--allowedTools", "Read"]
    elif agent == "codex":
        cmd = ["codex", "exec", prompt, "--json", "--ephemeral"]
    elif agent == "opencode":
        cmd = ["opencode", "run", prompt, "--format", "json"]
    else:
        raise ValueError(f"Unknown judge agent: {agent}")

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"Judge {agent} failed: {result.stderr[:500]}")
    return _parse_agent_output(agent, result.stdout)


def _parse_agent_output(agent: str, raw: str) -> dict:
    """Parse structured JSON output from coding agent CLI."""
    # For claude --output-format json, the result field contains the text
    if agent == "claude":
        try:
            wrapper = json.loads(raw)
            text = wrapper.get("result", raw) if isinstance(wrapper, dict) else raw
        except (json.JSONDecodeError, TypeError):
            text = raw
    else:
        text = raw

    # Try direct JSON parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to extracting JSON from markdown code blocks
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    raise RuntimeError(f"Could not parse JSON from {agent} output: {text[:300]}")


def build_judge_prompt(template_path: Path, trace_path: Path, pipeline_output: str) -> str:
    """Read judge prompt template and inject trace path + pipeline output."""
    template = template_path.read_text(encoding="utf-8")
    return template.replace("{trace_path}", str(trace_path)).replace("{output}", pipeline_output)


if __name__ == "__main__":
    """Self-test for judge utilities."""
    # Test _parse_agent_output with direct JSON
    assert _parse_agent_output("codex", '{"completeness": 0.8}') == {"completeness": 0.8}

    # Test _parse_agent_output with claude wrapper
    wrapper = json.dumps({"result": '{"completeness": 0.9}'})
    assert _parse_agent_output("claude", wrapper) == {"completeness": 0.9}

    # Test _parse_agent_output with markdown code block
    md = 'Some text\n```json\n{"clarity": 0.7}\n```\nmore text'
    assert _parse_agent_output("codex", md) == {"clarity": 0.7}

    # Test build_judge_prompt
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("Evaluate {trace_path}\nOutput: {output}")
        f.flush()
        prompt = build_judge_prompt(Path(f.name), Path("/tmp/trace.jsonl"), '{"data": 1}')
        assert "/tmp/trace.jsonl" in prompt
        assert '{"data": 1}' in prompt

    print("judge: self-test passed")
