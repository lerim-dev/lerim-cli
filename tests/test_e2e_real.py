"""Real-path end-to-end coverage for chat and extract pipeline flows."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import pytest

from lerim.config.settings import reload_config
from lerim.memory import extract_pipeline as pipeline
from lerim.sessions import catalog
from tests.helpers import run_cli, write_test_config


_HAS_ZAI = bool(os.environ.get("ZAI_API_KEY"))
_HAS_OPENAI = bool(os.environ.get("OPENAI_API_KEY"))


@pytest.mark.e2e
@pytest.mark.agent
@unittest.skipUnless(
    _HAS_ZAI and _HAS_OPENAI and os.environ.get("LERIM_E2E", ""),
    "Set LERIM_E2E=1 with ZAI_API_KEY and OPENAI_API_KEY to run E2E tests",
)
class TestE2EReal(unittest.TestCase):
    def test_chat_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_path = write_test_config(
                tmp_path,
                agent={
                    "provider": "openrouter",
                    "model": "qwen/qwen3-coder-30b-a3b-instruct",
                    "timeout": 120,
                },
                embeddings={"provider": "openai", "model": "text-embedding-3-small"},
            )
            os.environ["LERIM_CONFIG"] = str(config_path)
            reload_config()
            exit_code, output = run_cli(["chat", "Respond with exactly: OK"])

        self.assertEqual(exit_code, 0)
        self.assertIn("OK", output)
        self.assertGreater(len(output.strip()), 0)


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
        "_extract_candidates_with_rlm",
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
    result = pipeline.extract_memories_from_session_file(
        session_path,
        metadata={"run_id": "run-e2e-1"},
        metrics={},
    )

    assert len(result) == 1
    assert result[0]["title"] == "Queue lifecycle contract"


if __name__ == "__main__":
    unittest.main()
