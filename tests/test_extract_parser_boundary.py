"""test extract parser boundary."""

from __future__ import annotations

from pathlib import Path

from lerim.memory import extract_pipeline as pipeline


def test_pipeline_module_is_extract_boundary() -> None:
    source = Path(pipeline.__file__).read_text(encoding="utf-8")
    assert "dspy.RLM" in source
    assert "MemoryRepository" not in source
    assert "search_memory" not in source
    assert "LerimAgent" not in source


def test_pipeline_exports_expected_extract_functions() -> None:
    assert callable(pipeline.extract_memories_from_session_file)
