<p align="center">
  <img src="assets/lerim.png" alt="Lerim Logo" width="160">
</p>

<p align="center"><strong>Continual learning layer for coding agents.</strong></p>
<p align="center"><a href="https://lerim.dev/">lerim.dev</a></p>

Lerim is a continual learning layer that gives coding agents persistent memory across sessions. It watches your agent conversations (Claude Code, Codex, Cursor, OpenCode, ...), extracts decisions and learnings, and stores them as plain markdown files that both humans and agents can read. Memories are refined offline over time through merging, deduplication, archiving, and decay-based forgetting. You can query stored memories anytime to bring relevant past context into your current session.

## Summary

Lerim is file-first and primitive-first.

- Primitive folders: `decisions`, `learnings`, `summaries`
- Project memory first: `<repo>/.lerim/`
- Global fallback memory: `~/.lerim/`
- Search default: `files` (no index required)
- Orchestration runtime: `pydantic-ai` lead agent + read-only explorer subagent
- Extraction/summarization: `dspy.RLM` role-configured models (default Ollama `qwen3:8b`)
- Graph source of truth: explicit id/slug references (and `related` when present)

This keeps memory readable by humans and easy for agents to traverse.

Lead flow:

1. Extract candidates from transcript archive.
2. Lead agent orchestrates with runtime tools and delegates a read-only explorer subagent.
3. Lead runs deterministic decision policy for `add|update|no-op`.
4. Lead writes memory only through boundary-enforced runtime write/edit tools.
5. `sync` stays lightweight; `maintain` runs offline memory refinement (merge duplicates, archive low-value entries, consolidate related memories, apply time-based decay).

## Quick start

### 1. Install

```bash
pip install lerim
```

Lerim's extraction pipeline requires [Deno](https://deno.land/):

```bash
brew install deno
```

### 2. Connect your agent platforms and start the learning loop

```bash
lerim connect auto    # detect Claude Code, Codex, Cursor, OpenCode sessions
lerim daemon          # sync sessions + maintain memories in a continuous loop
```

That's it. Lerim now watches your sessions, extracts decisions and learnings, and refines them over time.

### 3. Teach your agent about Lerim

Install the Lerim skill so your agent knows how to query past context:

```bash
npx skills add lerim-dev/lerim-cli
```

This works with Claude Code, Codex, Cursor, Copilot, Cline, Windsurf, OpenCode, and [other agents that support skills](https://skills-ai.dev).

### 4. Get the most out of Lerim

At the start of a session, tell your agent:

> Check lerim for any relevant memories about [topic you're working on].

Your agent will run `lerim chat` or `lerim memory search` to pull in past decisions and learnings before it starts working.

## CLI reference

Full command reference: [`skills/lerim/cli-reference.md`](skills/lerim/cli-reference.md)

```bash
lerim connect auto                          # detect and connect platforms
lerim sync                                  # one-shot: sync sessions + extract
lerim maintain                              # one-shot: merge, archive, decay
lerim daemon                                # continuous sync + maintain loop
lerim chat "Why did we choose this?"        # query memories
lerim memory search "auth pattern"          # keyword search
lerim memory list                           # list all memories
lerim memory add --title "..." --body "..." # manual memory
lerim memory reset --scope both --yes       # wipe and start fresh
lerim dashboard                             # local web UI
lerim status                                # runtime state
```

### Development

```bash
uv venv && source .venv/bin/activate
uv pip install -e .
scripts/run_tests.sh unit
scripts/run_tests.sh all
```

### Configuration

TOML-layered config (low to high priority):

1. `src/lerim/config/default.toml` (shipped with package -- all defaults)
2. `~/.lerim/config.toml` (user global)
3. `<repo>/.lerim/config.toml` (project overrides)
4. `LERIM_CONFIG` env var path (explicit override, for CI/tests)

API keys come from environment variables only (`ZAI_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, optional `ANTHROPIC_API_KEY`).

### Tracing (OpenTelemetry)

Lerim uses PydanticAI's built-in OpenTelemetry instrumentation for agent observability.
Stderr logs are kept minimal; detailed traces (model calls, tool calls, tokens, timing)
go through OTel spans instead.

One-time setup:

```bash
uv pip install logfire
logfire auth
logfire projects new
```

Enable tracing:

```bash
# env var (quick toggle)
LERIM_TRACING=1 lerim sync

# or in config
# .lerim/config.toml
[tracing]
enabled = true
```

View traces at https://logfire.pydantic.dev.

Config options (`[tracing]` in TOML):

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enable tracing (or set `LERIM_TRACING=1`) |
| `include_httpx` | `false` | Capture raw HTTP request/response bodies |
| `include_content` | `true` | Include prompt/completion text in spans |

### Supported platforms

- `claude` — reads from `~/.claude/projects/` (JSONL files)
- `codex` — reads from `~/.codex/sessions/` (JSONL files)
- `cursor` — reads from Cursor's `state.vscdb` SQLite DB, exports sessions as JSONL to `~/.lerim/cache/cursor/`
- `opencode` — reads from `~/.local/share/opencode/`

### Search

Retrieval is file-first: scan markdown memory files directly. No index required.

## Memory layout

Project scope:

```text
<repo>/.lerim/
  config.toml              # project overrides
  memory/
    decisions/
    learnings/
    summaries/
      YYYYMMDD/
        HHMMSS/
          {slug}.md
    archived/
      decisions/
      learnings/
  meta/
    traces/
      sessions/
  workspace/
    sync-<YYYYMMDD-HHMMSS>-<shortid>/
      extract.json
      summary.json
      memory_actions.json
      agent.log
      subagents.log
      session.log
    maintain-<YYYYMMDD-HHMMSS>-<shortid>/
      maintain_actions.json
      agent.log
      subagents.log
  index/   # reserved
```

Global fallback scope follows the same layout under `~/.lerim/`.

## Primitive frontmatter (lean)

- `decision`: `id,title,created,updated,source,confidence,tags`
- `learning`: `id,title,created,updated,source,confidence,tags,kind`
- `summary`: `id,title,description,date,time,coding_agent,raw_trace_path,run_id,repo_name,created,source,tags`

All metadata lives in frontmatter — no sidecars.

## Reset policy

Memory reset is explicit and destructive.

- `lerim memory reset --scope project|global|both --yes`
- Deletes `memory/`, `workspace/`, and `index/` under selected root(s), then recreates canonical folders.
- `--scope project`: resets `<repo>/.lerim/` only.
- `--scope global`: resets `~/.lerim/` only (includes sessions DB).
- `--scope both` (default): resets both.
- Sessions DB lives in global `index/`, so `--scope project` alone does not reset sessions.

Fresh start:
```bash
lerim memory reset --yes        # wipe everything
lerim sync --max-sessions 5     # re-sync newest conversations
```

## Migration from Acreta

If you previously used Acreta, the data directories have moved from `~/.acreta/` to `~/.lerim/` and from `<repo>/.acreta/` to `<repo>/.lerim/`. Existing data is not migrated automatically. Run `lerim memory reset --yes && lerim sync` to start fresh.

## Docs

- Runtime architecture: `docs/architecture.md`
- CLI reference: `skills/lerim/cli-reference.md`
- Agent skill: `skills/lerim/SKILL.md`
