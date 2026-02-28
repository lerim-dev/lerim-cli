---
name: lerim
description: Persistent memory for coding agents. Query past decisions and learnings before starting work. Lerim watches your sessions, extracts what matters, and makes it available across every future session.
---

# Lerim

Lerim gives you persistent memory across sessions. It watches your conversations, extracts decisions and learnings, and stores them as plain markdown files you can query anytime.

## When to use

- **Before starting a task**: query Lerim for relevant past context.
- **When making a decision**: check if a similar decision was already made.
- **When debugging**: look up past learnings about the area you're working in.

## Commands

```bash
lerim memory search "authentication pattern"  # fast keyword search, returns raw matches
lerim ask "Why did we choose SQLite?"          # LLM-synthesized answer from memories
lerim memory list                              # list all memories
```

Use `memory search` for quick lookups when you can reason over the raw results. Use `ask` when you need a synthesized answer across multiple memories.

## How it works

Lerim runs in the background (via `lerim daemon`). It syncs your agent sessions, extracts decisions and learnings into `.lerim/memory/`, and refines them over time. Memories are plain markdown with YAML frontmatter.

Your job is to query existing memories when they're relevant. Setup (`pip install lerim`, `lerim connect auto`, `lerim daemon`) is done by the user before you start.

## References

- Full CLI reference: [cli-reference.md](cli-reference.md)
