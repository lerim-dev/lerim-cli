"""Tests for dashboard visual polish regressions in static HTML pages."""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


class TestUnifiedDashboardVisualPolish(unittest.TestCase):
    """Validate the main unified dashboard visual configuration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.content = _read("dashboard/index.html")

    def test_chart_axes_use_shared_no_splitline_defaults(self) -> None:
        self.assertIn("function chartAxisDefaults(extra = {})", self.content)
        self.assertIn("splitLine: { show: false }", self.content)

    def test_page_wrapper_background_is_transparent(self) -> None:
        self.assertIn(".page-wrapper,", self.content)
        self.assertIn("background: transparent !important;", self.content)

if __name__ == "__main__":
    unittest.main()
