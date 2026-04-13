# lerim ask

Ask a question using Lerim memory as context.

## Overview

`lerim ask` sends a question to the running Lerim service. The ask flow reads project memories and returns a grounded answer.

Default scope is all registered projects. Use `--scope project --project ...` to narrow.

!!! note
    This command requires a running Lerim server (`lerim up` or `lerim serve`).

## Syntax

```bash
lerim ask <question> [--scope all|project] [--project NAME] [--json]
```

## Parameters

| Parameter | Default | Description |
|---|---|---|
| `question` | required | The question to ask |
| `--scope` | `all` | Read from all projects or one project |
| `--project` | -- | Project name/path when `--scope=project` |
| `--json` | off | Output structured JSON payload |

## Examples

```bash
lerim ask "Why did we choose this architecture?"
lerim ask "What changed in auth recently?" --scope project --project lerim-cli
lerim ask "What should I watch out for?" --json
```

## How it works

1. CLI posts your question to `/api/ask`
2. Ask flow retrieves relevant memory files from selected scope
3. Model answers using retrieved memory context
4. CLI prints text (or full JSON when `--json`)

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Server or provider/auth error |
| `2` | Usage error |

## Related commands

<div class="grid cards" markdown>

-   :material-chart-box: **lerim status**

    ---

    Check project streams and queue health

    [:octicons-arrow-right-24: lerim status](status.md)

-   :material-format-list-bulleted: **lerim memory list**

    ---

    List memory files in scope

    [:octicons-arrow-right-24: lerim memory](memory.md)

</div>
