# Querying Memories

Lerim provides several ways to search and retrieve memories. All queries are
read-only and project-scoped.

## Reading memory files directly

The fastest way to access memories is to read them as files — no server needed.

### Start with the index

Read `.lerim/memory/index.md` at the start of each session. It is a categorized
table of contents listing every memory file with a one-line description and a
link to the file. Example:

```markdown
# Memory Index

## Feedback
- [maintain-algo-misses-stale-decisions](feedback_maintain_algo_misses_stale_decisions.md) -- ...

## Project
- [auth-middleware-rewrite](project_auth_middleware_rewrite.md) -- ...
```

From there, read individual `.md` files for full context on any topic relevant
to your current task. Each memory file has YAML frontmatter (`name`,
`description`, `type`) and a markdown body.

### Recommended CLAUDE.md / AGENTS.md snippet

Add the following to your project's `CLAUDE.md` or `AGENTS.md` so your coding
agent knows about Lerim from the start of every session:

```markdown
## Lerim Memory
This project uses Lerim for persistent memory across coding sessions.
At the start of each session, read `.lerim/memory/index.md` — it lists all
stored memories by category with one-line descriptions and links. Read
individual memory files for full context on past decisions and learnings.
For LLM-synthesized answers, use `lerim ask "your question"` (requires server).
For detailed usage, invoke the `/lerim` skill.
```

## `lerim ask` -- LLM-powered Q&A

The primary query interface. Sends your question to the lead agent with memory
context.

!!! note "Requires running server"
    `lerim ask` is a service command that requires `lerim up` or `lerim serve`
    to be running.

### Basic query

```bash
lerim ask "Why did we choose Postgres over SQLite?"
```

The lead agent retrieves relevant memories, uses them as context, and returns
a natural language answer with evidence of which memories were consulted.

### Limit context

Control how many memory items are included as context:

```bash
lerim ask "What auth pattern do we use?" --limit 5
```

| Flag | Default | Description |
|------|---------|-------------|
| `question` | required | Your question (quote if it contains spaces) |
| `--project` | -- | Scope to a specific project (not yet implemented) |
| `--limit` | `12` | Max memory items provided as context |

### JSON output

Get structured output for scripting or agent integration:

```bash
lerim ask "How is the database configured?" --json
```

Returns JSON with the answer, sources, and metadata.

## `lerim memory list` -- browse all memories

List stored memories (decisions and learnings), ordered by recency:

```bash
lerim memory list
```

```bash
lerim memory list --limit 10
```

```bash
lerim memory list --json
```

| Flag | Default | Description |
|------|---------|-------------|
| `--project` | -- | Filter to project (not yet implemented) |
| `--limit` | `50` | Max items |

## Tips for effective queries

### Be specific

```bash
# Good -- specific topic
lerim ask "What authentication pattern does the API use?"

# Less effective -- too broad
lerim ask "How does auth work?"
```

### Reference past decisions

```bash
lerim ask "Why did we switch from REST to gRPC for the internal API?"
lerim ask "What problems did we have with the original caching approach?"
```

### Check before implementing

At the start of a coding session, read `.lerim/memory/index.md` and drill into
any memories relevant to the task at hand. If you need a synthesized answer
across multiple memories, use `lerim ask`:

```bash
lerim ask "What was the rationale for the database migration strategy?"
```
