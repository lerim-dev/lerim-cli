"""Shared test utilities for constructing canonical runtime configuration."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from lerim.config.settings import Config, DSPyRoleConfig, LLMRoleConfig


def make_config(base: Path) -> Config:
    """Build a deterministic Config object rooted at ``base`` for tests."""
    return Config(
        data_dir=base,
        global_data_dir=base,
        memory_dir=base / "memory",
        index_dir=base / "index",
        memories_db_path=base / "index" / "memories.sqlite3",
        graph_db_path=base / "index" / "graph.sqlite3",
        sessions_db_path=base / "index" / "sessions.sqlite3",
        platforms_path=base / "platforms.json",
        memory_scope="global_only",
        memory_project_dir_name=".lerim",
        decay_enabled=True,
        decay_days=180,
        decay_min_confidence_floor=0.1,
        decay_archive_threshold=0.2,
        decay_recent_access_grace_days=30,
        server_host="127.0.0.1",
        server_port=8765,
        poll_interval_minutes=5,
        lead_role=LLMRoleConfig(
            provider="openrouter",
            model="qwen/qwen3-coder-30b-a3b-instruct",
            api_base="",
            fallback_models=(),
            timeout_seconds=300,
            max_iterations=24,
            openrouter_provider_order=("nebius",),
        ),
        explorer_role=LLMRoleConfig(
            provider="openrouter",
            model="qwen/qwen3-coder-30b-a3b-instruct",
            api_base="",
            fallback_models=(),
            timeout_seconds=180,
            max_iterations=16,
            openrouter_provider_order=("nebius",),
        ),
        extract_role=DSPyRoleConfig(
            provider="openrouter",
            model="qwen/qwen3-coder-30b-a3b-instruct",
            api_base="",
            timeout_seconds=180,
            max_iterations=24,
            max_llm_calls=24,
            sub_provider="openrouter",
            sub_model="qwen/qwen3-coder-30b-a3b-instruct",
            openrouter_provider_order=("nebius",),
        ),
        summarize_role=DSPyRoleConfig(
            provider="openrouter",
            model="qwen/qwen3-coder-30b-a3b-instruct",
            api_base="",
            timeout_seconds=180,
            max_iterations=24,
            max_llm_calls=24,
            sub_provider="openrouter",
            sub_model="qwen/qwen3-coder-30b-a3b-instruct",
            openrouter_provider_order=("nebius",),
        ),
        sync_window_days=7,
        sync_max_sessions=50,
        sync_max_workers=4,
        tracing_enabled=False,
        tracing_include_httpx=False,
        tracing_include_content=True,
        anthropic_api_key=None,
        openai_api_key=None,
        zai_api_key=None,
        openrouter_api_key=None,
    )


def write_test_config(tmp_path: Path, **sections: dict[str, Any]) -> Path:
    """Write a test config.toml pointing data dir to ``tmp_path``.

    Usage::

        write_test_config(tmp_path, agent={"provider": "anthropic"})
    """
    all_sections: dict[str, dict[str, Any]] = {
        "data": {"dir": str(tmp_path)},
        "memory": {"scope": "global_only"},
    }

    legacy_agent = sections.pop("agent", None)
    if isinstance(legacy_agent, dict):
        lead = all_sections.setdefault("roles.lead", {})
        if "provider" in legacy_agent:
            lead["provider"] = legacy_agent["provider"]
        if "model" in legacy_agent:
            lead["model"] = legacy_agent["model"]
        if "timeout" in legacy_agent:
            lead["timeout_seconds"] = legacy_agent["timeout"]

    legacy_dspy = sections.pop("dspy", None)
    if isinstance(legacy_dspy, dict):
        extract = all_sections.setdefault("roles.extract", {})
        summarize = all_sections.setdefault("roles.summarize", {})
        for key, value in legacy_dspy.items():
            mapped = {
                "provider": "provider",
                "model": "model",
                "api_base": "api_base",
                "rlm_max_iterations": "max_iterations",
                "rlm_max_llm_calls": "max_llm_calls",
            }.get(key)
            if mapped:
                extract[mapped] = value
                summarize[mapped] = value

    for name, payload in sections.items():
        if isinstance(payload, dict):
            all_sections[name] = payload

    lines: list[str] = []
    for section_name, fields in all_sections.items():
        lines.append(f"[{section_name}]")
        for key, value in fields.items():
            if isinstance(value, bool):
                lines.append(f"{key} = {'true' if value else 'false'}")
            elif isinstance(value, (int, float)):
                lines.append(f"{key} = {value}")
            else:
                lines.append(f'{key} = "{value}"')
        lines.append("")

    config_path = tmp_path / "test_config.toml"
    config_path.write_text("\n".join(lines), encoding="utf-8")
    return config_path


def run_cli(args: list[str]) -> tuple[int, str]:
    """Run CLI command and return ``(exit_code, stdout_text)``."""
    from lerim.app import cli

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(args)
    return code, out.getvalue()


def run_cli_json(args: list[str]) -> tuple[int, dict]:
    """Run CLI command and parse stdout JSON payload."""
    code, output = run_cli(args)
    return code, json.loads(output)
