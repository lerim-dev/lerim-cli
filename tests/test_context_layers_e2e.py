"""End-to-end checks for ask context behavior via HTTP API mock."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from tests.helpers import run_cli_json


@pytest.mark.e2e
class TestContextLayersE2E(unittest.TestCase):
    def test_ask_forwards_to_api_and_returns_answer(self) -> None:
        """Ask command forwards to HTTP API and returns the answer."""
        fake_response = {
            "answer": "answer",
            "agent_session_id": "sid-1",
            "memories_used": [],
            "error": False,
        }
        with patch("lerim.app.cli._api_post", return_value=fake_response):
            exit_code, payload = run_cli_json(["ask", "question", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["answer"], "answer")


if __name__ == "__main__":
    unittest.main()
