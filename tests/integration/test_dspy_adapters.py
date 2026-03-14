"""Layer 1: DSPy adapter integration tests for extraction and summarization.

Parametrized tests across 3 adapters (ChatAdapter, JSONAdapter, XMLAdapter),
2 DSPy modules (ChainOfThought, Predict), and 2 fixtures (simple, long).
Gated by LERIM_EVAL_OLLAMA=1. Uses qwen3.5:4b-q8_0 via Ollama.

Each test asserts that the pipeline produces non-empty, schema-valid output.
No adapter is favored — all are tested with identical assertions.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import dspy
import pytest

from lerim.memory.schemas import MemoryCandidate

pytestmark = pytest.mark.integration

_skip_no_ollama = pytest.mark.skipif(
    not os.environ.get("LERIM_EVAL_OLLAMA"),
    reason="LERIM_EVAL_OLLAMA not set",
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "traces"
SIMPLE_TRACE = FIXTURES_DIR / "claude_simple.jsonl"
LONG_TRACE = FIXTURES_DIR / "claude_long_multitopic.jsonl"

# Model used for all adapter tests (matches eval config)
OLLAMA_MODEL = os.environ.get("LERIM_EVAL_MODEL", "qwen3.5:4b-q8_0")
OLLAMA_BASE = os.environ.get("LERIM_EVAL_OLLAMA_BASE", "http://127.0.0.1:11434")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ollama_lm() -> dspy.LM:
    """Build DSPy LM for Ollama without config system."""
    return dspy.LM(
        f"ollama_chat/{OLLAMA_MODEL}",
        api_key="ollama",
        api_base=OLLAMA_BASE,
        cache=False,
        max_tokens=32000,
    )


def _get_adapter(name: str):
    """Return a DSPy adapter instance by name."""
    if name == "ChatAdapter":
        return dspy.ChatAdapter()
    if name == "JSONAdapter":
        return dspy.JSONAdapter()
    if name == "XMLAdapter":
        return dspy.XMLAdapter()
    raise ValueError(f"Unknown adapter: {name}")


def _get_module(name: str, signature):
    """Return a DSPy module instance by name."""
    if name == "ChainOfThought":
        return dspy.ChainOfThought(signature)
    if name == "Predict":
        return dspy.Predict(signature)
    raise ValueError(f"Unknown module: {name}")


def _setup_eval_config() -> tuple[Any, Path]:
    """Set up eval config pointing to a temp directory. Returns (config, temp_dir)."""
    temp_dir = Path(tempfile.mkdtemp(prefix="lerim_adapter_test_"))
    (temp_dir / "memory").mkdir()
    (temp_dir / "index").mkdir()

    from lerim.config.settings import build_eval_config, set_config_override

    roles = {
        "lead": {"provider": "ollama", "model": OLLAMA_MODEL, "thinking": False},
        "explorer": {"provider": "ollama", "model": OLLAMA_MODEL, "thinking": False},
        "extract": {
            "provider": "ollama",
            "model": OLLAMA_MODEL,
            "thinking": False,
            "max_window_tokens": 150000,
        },
        "summarize": {
            "provider": "ollama",
            "model": OLLAMA_MODEL,
            "thinking": False,
            "max_window_tokens": 150000,
        },
    }
    cfg = build_eval_config(roles, temp_dir)
    set_config_override(cfg)
    return cfg, temp_dir


def _cleanup_eval_config(temp_dir: Path) -> None:
    """Reset config override and clean temp dir."""
    from lerim.config.settings import set_config_override

    set_config_override(None)
    shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Extraction signature (duplicated from pipeline to allow direct module calls)
# ---------------------------------------------------------------------------

from lerim.memory.extract_pipeline import MemoryExtractSignature
from lerim.memory.summarization_pipeline import TraceSummarySignature


# ---------------------------------------------------------------------------
# Extraction adapter tests
# ---------------------------------------------------------------------------

ADAPTERS = ["ChatAdapter", "JSONAdapter", "XMLAdapter"]
MODULES = ["ChainOfThought", "Predict"]
FIXTURES = [
    ("simple", SIMPLE_TRACE),
    ("long", LONG_TRACE),
]


@_skip_no_ollama
@pytest.mark.parametrize("adapter_name", ADAPTERS)
@pytest.mark.parametrize("module_name", MODULES)
@pytest.mark.parametrize("fixture_name,fixture_path", FIXTURES)
def test_extraction_adapter(
    adapter_name: str, module_name: str, fixture_name: str, fixture_path: Path
) -> None:
    """Extraction with {adapter} + {module} on {fixture} produces valid candidates."""
    cfg, temp_dir = _setup_eval_config()
    try:
        transcript = fixture_path.read_text(encoding="utf-8")
        lm = _build_ollama_lm()
        adapter = _get_adapter(adapter_name)
        module = _get_module(module_name, MemoryExtractSignature)

        with dspy.context(lm=lm, adapter=adapter):
            result = module(
                transcript=transcript,
                guidance="",
            )

        primitives = getattr(result, "primitives", [])
        assert isinstance(primitives, list), (
            f"{adapter_name}+{module_name} on {fixture_name}: "
            f"primitives is {type(primitives)}, not list"
        )
        assert len(primitives) > 0, (
            f"{adapter_name}+{module_name} on {fixture_name}: "
            f"0 candidates extracted — pipeline produced nothing"
        )

        # Validate each candidate against MemoryCandidate schema
        for i, item in enumerate(primitives):
            if isinstance(item, MemoryCandidate):
                d = item.model_dump(mode="json", exclude_none=True)
            elif isinstance(item, dict):
                d = item
            else:
                pytest.fail(
                    f"{adapter_name}+{module_name} on {fixture_name}: "
                    f"candidate[{i}] is {type(item)}, not dict/MemoryCandidate"
                )
            MemoryCandidate.model_validate(d)
    finally:
        _cleanup_eval_config(temp_dir)


# ---------------------------------------------------------------------------
# Summarization adapter tests
# ---------------------------------------------------------------------------


@_skip_no_ollama
@pytest.mark.parametrize("adapter_name", ADAPTERS)
@pytest.mark.parametrize("module_name", MODULES)
@pytest.mark.parametrize("fixture_name,fixture_path", FIXTURES)
def test_summarization_adapter(
    adapter_name: str, module_name: str, fixture_name: str, fixture_path: Path
) -> None:
    """Summarization with {adapter} + {module} on {fixture} produces valid output."""
    cfg, temp_dir = _setup_eval_config()
    try:
        transcript = fixture_path.read_text(encoding="utf-8")
        lm = _build_ollama_lm()
        adapter = _get_adapter(adapter_name)
        module = _get_module(module_name, TraceSummarySignature)

        with dspy.context(lm=lm, adapter=adapter):
            result = module(
                transcript=transcript,
                guidance="",
            )

        payload = getattr(result, "summary_payload", None)
        assert payload is not None, (
            f"{adapter_name}+{module_name} on {fixture_name}: summary_payload is None"
        )

        # Normalize to dict
        from lerim.memory.summarization_pipeline import TraceSummaryCandidate

        if isinstance(payload, TraceSummaryCandidate):
            d = payload.model_dump(mode="json", exclude_none=True)
        elif isinstance(payload, dict):
            d = payload
            TraceSummaryCandidate.model_validate(d)
        else:
            pytest.fail(
                f"{adapter_name}+{module_name} on {fixture_name}: "
                f"summary_payload is {type(payload)}, not dict/TraceSummaryCandidate"
            )

        # Required fields
        for field in ("title", "description", "user_intent", "session_narrative"):
            assert field in d, f"Missing field: {field}"
            assert d[field], f"Empty field: {field}"

        # Word limits
        intent_words = len(d.get("user_intent", "").split())
        narrative_words = len(d.get("session_narrative", "").split())
        assert intent_words <= 150, f"user_intent has {intent_words} words (max 150)"
        assert narrative_words <= 200, (
            f"session_narrative has {narrative_words} words (max 200)"
        )
    finally:
        _cleanup_eval_config(temp_dir)


# ---------------------------------------------------------------------------
# Full pipeline tests (use production code path, not raw modules)
# ---------------------------------------------------------------------------


@_skip_no_ollama
@pytest.mark.parametrize("fixture_name,fixture_path", FIXTURES)
def test_extract_pipeline_full(fixture_name: str, fixture_path: Path) -> None:
    """Full extraction pipeline produces candidates on {fixture}."""
    cfg, temp_dir = _setup_eval_config()
    try:
        from lerim.memory.extract_pipeline import extract_memories_from_session_file

        result = extract_memories_from_session_file(fixture_path)
        assert isinstance(result, list)
        assert len(result) > 0, f"Full pipeline on {fixture_name}: 0 candidates"
        for item in result:
            MemoryCandidate.model_validate(item)
    finally:
        _cleanup_eval_config(temp_dir)


@_skip_no_ollama
@pytest.mark.parametrize("fixture_name,fixture_path", FIXTURES)
def test_summarize_pipeline_full(fixture_name: str, fixture_path: Path) -> None:
    """Full summarization pipeline produces valid output on {fixture}."""
    cfg, temp_dir = _setup_eval_config()
    try:
        from lerim.memory.summarization_pipeline import (
            summarize_trace_from_session_file,
        )

        result = summarize_trace_from_session_file(fixture_path)
        assert isinstance(result, dict)
        for field in ("title", "description", "user_intent", "session_narrative"):
            assert field in result, f"Missing field: {field}"
            assert result[field], f"Empty field: {field}"
    finally:
        _cleanup_eval_config(temp_dir)
