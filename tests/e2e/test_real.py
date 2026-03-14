"""Real-path end-to-end coverage for ask and extract pipeline flows."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lerim.config.settings import reload_config
from lerim.memory import extract_pipeline as pipeline
from lerim.sessions import catalog
from tests.helpers import write_test_config


pytestmark = pytest.mark.e2e

_skip = pytest.mark.skipif(
    not os.environ.get("LERIM_E2E"),
    reason="LERIM_E2E not set",
)


@_skip
def test_ask_end_to_end(tmp_path):
    """LerimAgent.ask returns a response from a real LLM."""
    from lerim.runtime.agent import LerimAgent

    agent = LerimAgent()
    response, session_id, cost_usd = agent.ask(
        "Respond with exactly: OK",
        memory_root=tmp_path,
    )
    assert isinstance(cost_usd, float)
    assert cost_usd >= 0.0
    assert isinstance(response, str)
    assert len(response.strip()) > 0


def test_extract_pipeline_end_to_end_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    catalog.init_sessions_db()

    session_path = tmp_path / "sessions" / "run-e2e-1.jsonl"
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        '{"role":"user","content":"keep queue lifecycle stable"}\n'
        '{"role":"assistant","content":"implemented heartbeat handling"}\n',
        encoding="utf-8",
    )
    catalog.index_session_for_fts(
        run_id="run-e2e-1",
        agent_type="codex",
        content="user asked for queue stability",
        session_path=str(session_path),
    )

    monkeypatch.setattr(
        pipeline,
        "_extract_candidates",
        lambda *_args, **_kwargs: [
            {
                "primitive": "learning",
                "title": "Queue lifecycle contract",
                "body": "Keep enqueue, claim, heartbeat, complete, and fail states consistent.",
                "confidence": 0.9,
                "kind": "pattern",
                "tags": ["queue-lifecycle"],
            }
        ],
    )
    result = pipeline.extract_memories_from_session_file(session_path)

    assert len(result) == 1
    assert result[0]["title"] == "Queue lifecycle contract"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
