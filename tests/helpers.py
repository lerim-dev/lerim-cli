"""Shared test utilities for constructing canonical runtime configuration."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from lerim.config.settings import Config, RoleConfig


def make_config(base: Path) -> Config:
    """Build a deterministic Config object rooted at ``base`` for tests."""
    return Config(
        data_dir=base,
        global_data_dir=base,
        memory_dir=base / "memory",
        index_dir=base / "index",
        sessions_db_path=base / "index" / "sessions.sqlite3",
        platforms_path=base / "platforms.json",
        memory_scope="global_only",
        memory_project_dir_name=".lerim",
        server_host="127.0.0.1",
        server_port=8765,
        sync_interval_minutes=5,
        maintain_interval_minutes=5,
        lead_role=RoleConfig(
            provider="openrouter",
            model="x-ai/grok-4.1-fast",
            timeout_seconds=300,
        ),
        sync_window_days=7,
        sync_max_sessions=50,
        parallel_pipelines=True,
        tracing_enabled=False,
        tracing_include_httpx=False,
        tracing_include_content=True,
        anthropic_api_key=None,
        openai_api_key=None,
        zai_api_key=None,
        openrouter_api_key=None,
        minimax_api_key=None,
        opencode_api_key=None,
        provider_api_bases={
            "minimax": "https://api.minimax.io/v1",
            "zai": "https://api.z.ai/api/coding/paas/v4",
            "openai": "https://api.openai.com/v1",
            "openrouter": "https://openrouter.ai/api/v1",
            "ollama": "http://127.0.0.1:11434",
            "mlx": "http://127.0.0.1:8000/v1",
        },
        auto_unload=True,
        agents={},
        projects={},
        cloud_endpoint="https://api.lerim.dev",
        cloud_token=None,
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
    from lerim.server import cli

    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(args)
    return code, out.getvalue()


def run_cli_json(args: list[str]) -> tuple[int, dict]:
    """Run CLI command and parse stdout JSON payload."""
    code, output = run_cli(args)
    return code, json.loads(output)
