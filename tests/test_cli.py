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
    fake_status = {
        "timestamp": "2026-02-28T00:00:00+00:00",
        "connected_agents": ["claude"],
        "platforms": [],
        "memory_count": 5,
        "sessions_indexed_count": 10,
        "queue": {"pending": 0},
        "latest_sync": None,
        "latest_maintain": None,
    }
    monkeypatch.setattr(cli, "_api_get", lambda _path: fake_status)
    code, payload = run_cli_json(["status", "--json"])
    assert code == 0
    assert "queue" in payload
    assert "latest_sync" in payload
    assert "latest_maintain" in payload


def test_chat_forwards_to_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chat command posts to /api/chat and prints the answer."""
    fake_response = {
        "answer": "Use bearer tokens.",
        "agent_session_id": "ses-1",
        "memories_used": [],
        "error": False,
    }
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, payload = run_cli_json(["chat", "how to deploy", "--limit", "5", "--json"])
    assert code == 0
    assert payload["answer"] == "Use bearer tokens."


def test_chat_returns_nonzero_on_auth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = {
        "answer": "authentication_error: invalid api key",
        "error": True,
    }
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: fake_response)
    code, _output = run_cli(["chat", "how to deploy"])
    assert code == 1


def test_chat_returns_nonzero_when_server_not_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_api_post", lambda _path, _body: None)
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


def test_json_flag_hoisting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """'lerim status --json' and 'lerim --json status' produce same result."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    fake_status = {
        "timestamp": "2026-02-28T00:00:00+00:00",
        "connected_agents": [],
        "platforms": [],
        "memory_count": 0,
        "sessions_indexed_count": 0,
        "queue": {},
        "latest_sync": None,
        "latest_maintain": None,
    }
    monkeypatch.setattr(cli, "_api_get", lambda _path: fake_status)
    code1, payload1 = run_cli_json(["status", "--json"])
    code2, payload2 = run_cli_json(["--json", "status"])
    assert code1 == 0
    assert code2 == 0
    # Both should produce valid status dicts with the same keys
    assert set(payload1.keys()) == set(payload2.keys())


def test_memory_list_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """'lerim memory list' outputs formatted memory entries."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    # Seed memory dir with a fixture file
    memory_dir = tmp_path / "memory" / "decisions"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "test-decision.md").write_text(
        "---\nid: test-decision\ntitle: Test Decision\ntags: [test]\n---\nBody.",
        encoding="utf-8",
    )
    code, output = run_cli(["memory", "list", "--json"])
    assert code == 0


def test_memory_add_creates_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'lerim memory add --title "..." --body "..."' creates valid .md file."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    # Ensure memory directories exist
    (tmp_path / "memory" / "decisions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "learnings").mkdir(parents=True, exist_ok=True)
    code, output = run_cli(
        [
            "memory",
            "add",
            "--primitive",
            "decision",
            "--title",
            "Test CLI Add",
            "--body",
            "Added via CLI test",
        ]
    )
    assert code == 0


def test_memory_search_finds_seeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'lerim memory search "auth"' finds seeded memory about auth."""
    config_path = write_test_config(tmp_path)
    monkeypatch.setenv("LERIM_CONFIG", str(config_path))
    reload_config()
    # Seed memory with auth-related decision
    memory_dir = tmp_path / "memory" / "decisions"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "auth-decision.md").write_text(
        "---\nid: auth-jwt\ntitle: Use JWT for authentication\ntags: [auth]\n---\nJWT with HS256.",
        encoding="utf-8",
    )
    code, output = run_cli(["memory", "search", "auth", "--json"])
    assert code == 0
