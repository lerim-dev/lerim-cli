---
name: lerim
description: Persistent memory for coding agents. Query past decisions and learnings before starting work. Lerim watches your sessions, extracts what matters, and makes it available across every future session.
---

# Lerim

Lerim gives you persistent memory across sessions. It watches your conversations, extracts decisions and learnings, and stores them as plain markdown files you can query anytime.

## Start here

At the beginning of each session, read `.lerim/memory/index.md`. It is a categorized
table of contents listing every memory file with a one-line description and a link.
From there, read individual `.md` files for full context on any topic that is relevant
to your current task.

This works without a server — it is just reading files from disk.

## When to use

- **Before starting a task**: read `index.md`, then drill into relevant memory files.
- **When making a decision**: check if a similar decision was already made.
- **When debugging**: look up past learnings about the area you're working in.

## Commands

```bash
lerim ask "Why did we choose SQLite?"   # LLM-synthesized answer from memories (requires server)
lerim memory list                       # list all memories (no server needed)
```

Use `lerim ask` when you need a synthesized answer across multiple memories.
Use `lerim memory list` to browse all memories by recency.

For most tasks, reading `index.md` + individual memory files directly is faster
and does not require the server to be running.

## How it works

Lerim runs in the background (via `lerim up` or `lerim serve`). It syncs your agent sessions, extracts decisions and learnings into `.lerim/memory/`, and refines them over time. Memories are plain markdown with YAML frontmatter.

Your job is to read and query existing memories when they are relevant. You do not write memories — Lerim handles extraction automatically. Setup (`pip install lerim`, `lerim init`, `lerim project add .`, `lerim up`) is done by the user before you start.

## References

- Full CLI reference: [cli-reference.md](cli-reference.md)
