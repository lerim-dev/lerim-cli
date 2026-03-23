"""Thin wrapper that invokes coding agent CLIs as judge.

Supports claude, codex, and opencode as judge agents. Reads prompt templates
from judge_prompts/ and injects trace path + pipeline output. Uses structured
output flags (--json-schema for claude, --output-schema + -o for codex) and
embeds schema instructions in prompt for opencode. Validates parsed results
against schema and retries on parse/validation failures.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from lerim.config.logging import logger


# JSON schema for judge responses with clarity dimension (extraction, summarization)
JUDGE_SCHEMA_CLARITY = {
	"type": "object",
	"properties": {
		"completeness": {"type": "number"},
		"faithfulness": {"type": "number"},
		"clarity": {"type": "number"},
		"precision": {"type": "number"},
		"reasoning": {"type": "string"},
	},
	"required": ["completeness", "faithfulness", "clarity", "precision", "reasoning"],
	"additionalProperties": False,
}

# JSON schema for judge responses with coherence dimension (lifecycle sync/maintain)
JUDGE_SCHEMA_COHERENCE = {
	"type": "object",
	"properties": {
		"completeness": {"type": "number"},
		"faithfulness": {"type": "number"},
		"coherence": {"type": "number"},
		"precision": {"type": "number"},
		"reasoning": {"type": "string"},
	},
	"required": ["completeness", "faithfulness", "coherence", "precision", "reasoning"],
	"additionalProperties": False,
}

MAX_RETRIES = 2


def _run_with_heartbeat(
    cmd: list[str], timeout: int, interval: int = 30
) -> subprocess.CompletedProcess:
    """Run a subprocess with periodic heartbeat logs.

    Uses Popen + a daemon thread that logs every ``interval`` seconds
    so long-running judge calls don't appear stuck.
    """
    stop = threading.Event()
    start = time.time()

    def _heartbeat():
        while not stop.wait(interval):
            logger.info(
                "  Judge still running... ({:.0f}s elapsed)", time.time() - start
            )

    t = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    finally:
        stop.set()
        t.join(timeout=2)


def _build_cmd(
    agent: str, prompt: str, model: str | None, schema: dict | None
) -> tuple[list[str], Path | None, Path | None]:
    """Build CLI command and optional temp files.

    Returns (cmd, temp_schema_path, temp_output_path). Caller must delete
    temp files if not None.
    """
    temp_schema_path: Path | None = None
    temp_output_path: Path | None = None

    if agent == "claude":
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--allowedTools",
            "Read",
        ]
        if model:
            cmd.extend(["--model", model])
        if schema:
            cmd.extend(["--json-schema", json.dumps(schema)])

    elif agent == "codex":
        cmd = ["codex", "exec", prompt, "--full-auto", "--ephemeral"]
        if model:
            cmd.extend(["--model", model])
        if schema:
            sf = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json",
                prefix="judge_schema_",
                delete=False,
            )
            json.dump(schema, sf)
            sf.close()
            temp_schema_path = Path(sf.name)
            cmd.extend(["--output-schema", str(temp_schema_path)])
            # Capture final structured answer to a file instead of JSONL stdout
            of = tempfile.NamedTemporaryFile(
                suffix=".json",
                prefix="judge_output_",
                delete=False,
            )
            of.close()
            temp_output_path = Path(of.name)
            cmd.extend(["-o", str(temp_output_path)])

    elif agent == "opencode":
        # Embed schema in prompt since opencode has no schema enforcement flag
        if schema:
            prompt += (
                "\n\nIMPORTANT: You MUST respond with ONLY a JSON object matching "
                "this exact schema, no other text:\n" + json.dumps(schema, indent=2)
            )
        cmd = ["opencode", "run", prompt, "--format", "json"]
        if model:
            cmd.extend(["--model", model])

    else:
        raise ValueError(f"Unknown judge agent: {agent}")

    return cmd, temp_schema_path, temp_output_path


def invoke_judge(
    agent: str,
    prompt: str,
    timeout: int = 120,
    model: str | None = None,
    schema: dict | None = None,
) -> dict:
    """Invoke a coding agent CLI as judge, return parsed JSON.

    Retries up to MAX_RETRIES times on JSON parse or validation failures.
    Uses structured output flags when available (--json-schema for claude,
    --output-schema + -o for codex). For opencode, embeds schema in prompt.
    """
    if schema is None:
        schema = JUDGE_SCHEMA_CLARITY

    last_error: RuntimeError | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        cmd, temp_schema_path, temp_output_path = _build_cmd(
            agent, prompt, model, schema
        )
        try:
            result = _run_with_heartbeat(cmd, timeout)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Judge {agent} failed (rc={result.returncode}): "
                    f"{result.stderr[:500]}"
                )
            # For codex -o flag, prefer the output file over JSONL stdout
            if (
                temp_output_path
                and temp_output_path.exists()
                and temp_output_path.stat().st_size > 0
            ):
                raw = temp_output_path.read_text(encoding="utf-8")
            else:
                raw = result.stdout
            parsed = _parse_agent_output(agent, raw)
            _validate_judge_result(parsed, schema)
            return parsed
        except RuntimeError as e:
            last_error = e
            retryable = "Could not parse JSON" in str(e) or "Judge result" in str(e)
            if retryable and attempt < MAX_RETRIES:
                logger.warning(
                    "Judge parse error (attempt {}/{}), retrying: {}",
                    attempt,
                    MAX_RETRIES,
                    e,
                )
                continue
            raise
        finally:
            if temp_schema_path and temp_schema_path.exists():
                temp_schema_path.unlink()
            if temp_output_path and temp_output_path.exists():
                temp_output_path.unlink()

    raise last_error  # type: ignore[misc]


def _extract_opencode_response(raw: str) -> str:
    """Extract final assistant response from opencode JSONL event stream.

    Scans lines in reverse to find the last event containing text content,
    which should be the final assistant response.
    """
    for line in reversed(raw.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                continue
            for key in ("content", "text", "message", "result", "output"):
                val = event.get(key)
                if isinstance(val, str) and len(val.strip()) > 2:
                    return val
        except (json.JSONDecodeError, TypeError):
            continue
    return raw


def _parse_agent_output(agent: str, raw: str) -> dict:
    """Parse structured JSON output from coding agent CLI."""
    # Claude --output-format json wraps in {"result": ..., "structured_output": ...}
    if agent == "claude":
        try:
            wrapper = json.loads(raw)
            if isinstance(wrapper, dict):
                # Prefer structured_output (set by --json-schema)
                structured = wrapper.get("structured_output")
                if isinstance(structured, dict):
                    return structured
                text = wrapper.get("result", raw)
            else:
                text = raw
        except (json.JSONDecodeError, TypeError):
            text = raw
    elif agent == "opencode":
        # --format json emits JSONL events; extract assistant response text
        text = _extract_opencode_response(raw)
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


def _validate_judge_result(result: dict, schema: dict | None) -> None:
    """Validate parsed judge result has required keys with correct types."""
    if not schema:
        return
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    missing = [k for k in required if k not in result]
    if missing:
        raise RuntimeError(
            f"Judge result missing required keys: {missing} "
            f"(got: {list(result.keys())})"
        )
    type_map: dict[str, tuple[type, ...]] = {
        "number": (int, float),
        "string": (str,),
    }
    for key in required:
        expected = properties.get(key, {}).get("type")
        allowed = type_map.get(expected)  # type: ignore[arg-type]
        if allowed and not isinstance(result[key], allowed):
            raise RuntimeError(
                f"Judge result key '{key}' expected {expected}, "
                f"got {type(result[key]).__name__}: {result[key]!r}"
            )


def build_judge_prompt(
    template_path: Path, trace_path: Path, pipeline_output: str
) -> str:
    """Read judge prompt template and inject trace path + pipeline output."""
    template = template_path.read_text(encoding="utf-8")
    return template.replace("{trace_path}", str(trace_path)).replace(
        "{output}", pipeline_output
    )


if __name__ == "__main__":
    """Self-test for judge utilities."""
    # Test _parse_agent_output with direct JSON
    assert _parse_agent_output("codex", '{"completeness": 0.8}') == {
        "completeness": 0.8
    }

    # Test _parse_agent_output with claude structured_output field (--json-schema)
    wrapper = json.dumps(
        {
            "result": "Done! Returned the evaluation.",
            "structured_output": {
                "completeness": 0.9,
                "faithfulness": 0.8,
                "clarity": 0.7,
                "precision": 0.6,
                "reasoning": "test",
            },
        }
    )
    assert _parse_agent_output("claude", wrapper) == {
        "completeness": 0.9,
        "faithfulness": 0.8,
        "clarity": 0.7,
        "precision": 0.6,
        "reasoning": "test",
    }

    # Test _parse_agent_output with claude result fallback (no structured_output)
    wrapper = json.dumps({"result": '{"completeness": 0.9}'})
    assert _parse_agent_output("claude", wrapper) == {"completeness": 0.9}

    # Test _parse_agent_output with markdown code block
    md = 'Some text\n```json\n{"clarity": 0.7}\n```\nmore text'
    assert _parse_agent_output("codex", md) == {"clarity": 0.7}

    # Test _extract_opencode_response with JSONL events
    events = (
        '{"type": "status", "status": "running"}\n'
        '{"type": "message", "content": "Let me analyze..."}\n'
        '{"type": "message", "content": "{\\"completeness\\": 0.9, '
        '\\"faithfulness\\": 0.8, \\"clarity\\": 0.7, '
        '\\"reasoning\\": \\"Good extraction.\\"}"}\n'
    )
    extracted = _extract_opencode_response(events)
    parsed = json.loads(extracted)
    assert parsed["completeness"] == 0.9

    # Test _parse_agent_output for opencode with JSONL events
    result = _parse_agent_output("opencode", events)
    assert result["completeness"] == 0.9

    # Test _validate_judge_result with valid result
    valid = {
        "completeness": 0.9,
        "faithfulness": 0.8,
        "clarity": 0.7,
        "precision": 0.6,
        "reasoning": "test",
    }
    _validate_judge_result(valid, JUDGE_SCHEMA_CLARITY)  # should not raise

    # Test _validate_judge_result with missing key
    try:
        _validate_judge_result({"completeness": 0.9}, JUDGE_SCHEMA_CLARITY)
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "missing required keys" in str(e)

    # Test _validate_judge_result with wrong type
    try:
        bad = {
            "completeness": "high",
            "faithfulness": 0.8,
            "clarity": 0.7,
            "precision": 0.6,
            "reasoning": "test",
        }
        _validate_judge_result(bad, JUDGE_SCHEMA_CLARITY)
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "expected number" in str(e)

    # Test build_judge_prompt
    import tempfile as _tmp

    with _tmp.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("Evaluate {trace_path}\nOutput: {output}")
        f.flush()
        prompt = build_judge_prompt(
            Path(f.name), Path("/tmp/trace.jsonl"), '{"data": 1}'
        )
        assert "/tmp/trace.jsonl" in prompt
        assert '{"data": 1}' in prompt

    # Test _build_cmd with schema for claude
    cmd, schema_tmp, output_tmp = _build_cmd(
        "claude", "test prompt", None, JUDGE_SCHEMA_CLARITY
    )
    assert "--json-schema" in cmd
    assert schema_tmp is None
    assert output_tmp is None

    # Test _build_cmd with schema for codex (--full-auto, --output-schema, -o, no --json)
    cmd, schema_tmp, output_tmp = _build_cmd(
        "codex", "test prompt", None, JUDGE_SCHEMA_CLARITY
    )
    assert "--output-schema" in cmd
    assert "-o" in cmd
    assert "--full-auto" in cmd
    assert "--json" not in cmd
    assert schema_tmp is not None
    assert output_tmp is not None
    schema_tmp.unlink()
    output_tmp.unlink()

    # Test _build_cmd for opencode embeds schema in prompt
    cmd, schema_tmp, output_tmp = _build_cmd(
        "opencode", "test prompt", None, JUDGE_SCHEMA_CLARITY
    )
    assert schema_tmp is None
    assert output_tmp is None
    prompt_in_cmd = cmd[2]  # opencode run <prompt>
    assert "MUST respond with ONLY a JSON object" in prompt_in_cmd
    assert '"completeness"' in prompt_in_cmd

    # Test schema constants have right keys
    assert set(JUDGE_SCHEMA_CLARITY["required"]) == {
        "completeness",
        "faithfulness",
        "clarity",
        "precision",
        "reasoning",
    }
    assert set(JUDGE_SCHEMA_COHERENCE["required"]) == {
        "completeness",
        "faithfulness",
        "coherence",
        "precision",
        "reasoning",
    }

    print("judge: self-test passed")
