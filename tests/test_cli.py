"""CLI parser and command-contract tests."""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from lerim.app import cli
from lerim.config.project_scope import ScopeResolution
from lerim.config.settings import reload_config
from tests.helpers import make_config, run_cli, run_cli_json, write_test_config


def test_help_lists_minimal_commands() -> None:
    parser = cli.build_parser()
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0
    text = out.getvalue()
    for command in (
        "connect",
        "sync",
        "maintain",
        "daemon",
        "dashboard",
        "memory",
        "chat",
        "status",
    ):
        assert command in text
    # Verify removed subcommands don't appear in the subcommand list.
    # Check the {connect,sync,...} subcommand choices section, not the full text
    # (description text may legitimately use these words).
    subcommand_choices = text.split("{")[1].split("}")[0] if "{" in text else ""
    for removed in ("readiness", "admin", "sessions", "config"):
        assert removed not in subcommand_choices, (
            f"removed command '{removed}' still in subcommands"
        )


def test_sync_parser_accepts_canonical_flags() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(
        ["sync", "--run-id", "run-1", "--agent", "claude,codex", "--window", "7d"]
    )
    assert isinstance(args, argparse.Namespace)
    assert args.command == "sync"
    assert args.run_id == "run-1"
    assert args.agent == "claude,codex"
    assert args.window == "7d"


def test_chat_parser_minimal_surface() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["chat", "what failed?", "--limit", "5"])
    assert args.command == "chat"
    assert args.question == "what failed?"
    assert args.limit == 5


def test_removed_command_rejected() -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["sessions"])
    assert exc.value.code == 2


def test_status_json_output_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    code, payload = run_cli_json(["status", "--json"])
    assert code == 0
    assert "queue" in payload
    assert "latest_sync" in payload
    assert "latest_maintain" in payload


def test_chat_uses_context_docs_when_memory_signal_is_thin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "search_memory", lambda *_args, **_kwargs: [])

    captured: dict[str, str] = {}

    class _FakeAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def chat(self, prompt: str, cwd: str | None = None, **_kwargs):
            _ = cwd
            captured["prompt"] = prompt
            return "answer", "agent-session-1"

    monkeypatch.setattr(cli, "LerimAgent", _FakeAgent)

    code, payload = run_cli_json(["chat", "how to deploy", "--limit", "5", "--json"])
    assert code == 0

    assert "Context docs (loaded only if needed)" in captured["prompt"]
    assert "memory-explorer" in captured["prompt"]


def test_chat_returns_nonzero_on_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeAgent:
        def __init__(self, **_kwargs) -> None:
            pass

        def chat(self, prompt: str, cwd: str | None = None, **_kwargs):
            _ = (prompt, cwd)
            return "authentication_error: invalid api key", "agent-session-1"

    monkeypatch.setattr(cli, "LerimAgent", _FakeAgent)
    code, _output = run_cli(["chat", "how to deploy"])
    assert code == 1


def test_memory_reset_recreates_project_and_global_roots(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_root = tmp_path / "project-data"
    global_root = tmp_path / "global-data"
    for root in (project_root, global_root):
        (root / "memory" / "learnings").mkdir(parents=True, exist_ok=True)
        (root / "memory" / "learnings" / "seed.md").write_text("seed", encoding="utf-8")
        (root / "index").mkdir(parents=True, exist_ok=True)
        (root / "index" / "fts.sqlite3").write_text("", encoding="utf-8")

    base_cfg = make_config(global_root)
    cfg = replace(base_cfg, data_dir=global_root, global_data_dir=global_root)

    monkeypatch.setattr(cli, "get_config", lambda: cfg)
    monkeypatch.setattr(
        cli,
        "resolve_data_dirs",
        lambda **_kwargs: ScopeResolution(
            project_root=tmp_path,
            project_data_dir=project_root,
            global_data_dir=global_root,
            ordered_data_dirs=[project_root, global_root],
        ),
    )

    code, payload = run_cli_json(
        ["memory", "reset", "--scope", "both", "--yes", "--json"]
    )
    assert code == 0
    assert len(payload["reset"]) == 2
    assert (project_root / "memory" / "learnings").exists()
    assert (global_root / "memory" / "learnings").exists()
    assert not (project_root / "memory" / "learnings" / "seed.md").exists()
    assert not (global_root / "memory" / "learnings" / "seed.md").exists()
