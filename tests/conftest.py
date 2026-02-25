"""Shared test fixtures for Lerim test suite.

Provides temp directories, seeded memories, and auto-applies a test config
(ollama/qwen3:4b) for smoke/integration/e2e tests via LERIM_CONFIG env var.
"""

import os
from pathlib import Path

import pytest

from tests.helpers import make_config


FIXTURES_DIR = Path(__file__).parent / "fixtures"
TRACES_DIR = FIXTURES_DIR / "traces"
MEMORIES_DIR = FIXTURES_DIR / "memories"
TEST_CONFIG_PATH = Path(__file__).parent / "test_config.toml"


def _needs_llm_config(items) -> bool:
    """Return True if any collected test has a smoke/integration/e2e marker."""
    for item in items:
        markers = {m.name for m in item.iter_markers()}
        if markers & {"smoke", "integration", "e2e"}:
            return True
    return False


def _build_test_config_toml(tmp_dir: Path) -> Path:
    """Build a test config TOML that uses test_config.toml as base with env var overrides.

    Supports LERIM_TEST_PROVIDER and LERIM_TEST_MODEL env vars to override
    the default ollama/qwen3:4b for all roles.
    """
    provider = os.environ.get("LERIM_TEST_PROVIDER", "").strip()
    model = os.environ.get("LERIM_TEST_MODEL", "").strip()
    if not provider and not model:
        return TEST_CONFIG_PATH

    # Read base config and override provider/model
    import tomllib

    with TEST_CONFIG_PATH.open("rb") as f:
        base = tomllib.load(f)
    roles = base.get("roles", {})
    for role_name in ("lead", "explorer", "extract", "summarize"):
        role = roles.get(role_name, {})
        if provider:
            role["provider"] = provider
        if model:
            role["model"] = model
        roles[role_name] = role
    base["roles"] = roles
    base.setdefault("data", {})["dir"] = str(tmp_dir)

    # Write merged config
    lines: list[str] = []
    for section, fields in base.items():
        if isinstance(fields, dict):
            # Handle nested sections like roles.lead
            has_nested = any(isinstance(v, dict) for v in fields.values())
            if has_nested:
                for sub_name, sub_fields in fields.items():
                    if isinstance(sub_fields, dict):
                        lines.append(f"[{section}.{sub_name}]")
                        for k, v in sub_fields.items():
                            lines.append(f"{k} = {_toml_value(v)}")
                        lines.append("")
                    else:
                        lines.append(f"[{section}]")
                        lines.append(f"{sub_name} = {_toml_value(sub_fields)}")
                        lines.append("")
            else:
                lines.append(f"[{section}]")
                for k, v in fields.items():
                    lines.append(f"{k} = {_toml_value(v)}")
                lines.append("")

    out_path = tmp_dir / "test_config.toml"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def _toml_value(v) -> str:
    """Format a Python value as TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return f'"{v}"'


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "smoke: Quick LLM sanity checks (requires ollama)"
    )


def pytest_collection_modifyitems(config, items):
    """Auto-apply test config (ollama/qwen3:4b) when LLM tests are collected."""
    if not _needs_llm_config(items):
        return

    # Only set LERIM_CONFIG if not already explicitly set by the user
    if os.environ.get("LERIM_CONFIG"):
        return

    tmp_dir = Path(config.rootdir) / ".pytest_tmp"
    tmp_dir.mkdir(exist_ok=True)
    cfg_path = _build_test_config_toml(tmp_dir)
    os.environ["LERIM_CONFIG"] = str(cfg_path)

    from lerim.config.settings import reload_config

    reload_config()


@pytest.fixture
def tmp_lerim_root(tmp_path):
    """Temporary Lerim data root with canonical folder structure."""
    memory_dir = tmp_path / "memory"
    for sub in (
        "decisions",
        "learnings",
        "summaries",
        "archived/decisions",
        "archived/learnings",
    ):
        (memory_dir / sub).mkdir(parents=True)
    (tmp_path / "workspace").mkdir()
    (tmp_path / "index").mkdir()
    return tmp_path


@pytest.fixture
def tmp_config(tmp_path, tmp_lerim_root):
    """Temporary config pointing at tmp_lerim_root."""
    return make_config(tmp_lerim_root)


@pytest.fixture
def seeded_memory(tmp_lerim_root):
    """tmp_lerim_root with fixture memory files copied in."""
    decisions_dir = tmp_lerim_root / "memory" / "decisions"
    learnings_dir = tmp_lerim_root / "memory" / "learnings"
    for src in MEMORIES_DIR.glob("decision_*.md"):
        (decisions_dir / src.name).write_text(src.read_text())
    for src in MEMORIES_DIR.glob("learning_*.md"):
        (learnings_dir / src.name).write_text(src.read_text())
    return tmp_lerim_root


def skip_unless_env(var_name):
    """Skip test unless environment variable is set."""
    return pytest.mark.skipif(
        not os.environ.get(var_name),
        reason=f"{var_name} not set",
    )
