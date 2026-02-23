"""test dashboard read only contract."""

from __future__ import annotations

from pathlib import Path


def test_dashboard_read_only_api_contract() -> None:
    source = (
        Path(__file__).parent.parent / "src" / "lerim" / "app" / "dashboard.py"
    ).read_text(encoding="utf-8")
    assert 'if path in {"/api/refine/run", "/api/reflect"}:' in source
    assert "READ_ONLY_MESSAGE" in source
    assert "def do_PUT(self)" in source
    assert "def do_PATCH(self)" in source
    assert "def do_DELETE(self)" in source
