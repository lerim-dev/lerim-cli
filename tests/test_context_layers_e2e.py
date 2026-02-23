"""End-to-end checks for chat context behavior when memory retrieval is thin."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import make_config, run_cli_json


@pytest.mark.e2e
class TestContextLayersE2E(unittest.TestCase):
    def test_chat_loads_context_docs_when_memory_is_thin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = make_config(Path(tmp))
            config.index_dir.mkdir(parents=True, exist_ok=True)

            class _FakeAgent:
                def __init__(self, **_kwargs) -> None:
                    pass

                def chat(self, prompt: str, cwd: str | None = None, **_kwargs):
                    _ = (prompt, cwd)
                    return "answer", "sid-1"

            with (
                patch("lerim.app.cli.get_config", return_value=config),
                patch("lerim.app.cli.search_memory", return_value=[]),
                patch("lerim.app.cli.LerimAgent", _FakeAgent),
            ):
                exit_code, payload = run_cli_json(["chat", "question", "--json"])

            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["response"], "answer")
            self.assertEqual(payload.get("context_doc_ids", []), [])


if __name__ == "__main__":
    unittest.main()
