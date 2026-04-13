# lerim project

Register, list, and remove projects tracked by Lerim.

## Overview

Manage which repositories Lerim tracks. Each project gets a `.lerim/` directory for its memories. Adding or removing a project restarts the Docker container to update volume mounts.

!!! info "Host-only command"
    This command runs on the host machine. It does not require a running Lerim server.

## Syntax

```bash
lerim project add <path>
lerim project list
lerim project remove <name>
```

## Subcommands

### `lerim project add`

Register a project directory:

```bash
lerim project add ~/codes/my-app       # register a project
lerim project add .                     # register current directory
```

This creates a `.lerim/` directory in the project root for storing memories.

### `lerim project list`

List all registered projects:

```bash
lerim project list
```

### `lerim project remove`

Unregister a project:

```bash
lerim project remove my-app            # unregister by name
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">path</span>
    <span class="param-type">string</span>
    <span class="param-badge required">required (add)</span>
  </div>
  <p class="param-desc">Filesystem path to the project directory. Tilde (~) is expanded.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">name</span>
    <span class="param-type">string</span>
    <span class="param-badge required">required (remove)</span>
  </div>
  <p class="param-desc">Project name as shown in <code>lerim project list</code>.</p>
</div>

## Examples

```bash
# Register multiple projects
lerim project add ~/codes/frontend
lerim project add ~/codes/backend
lerim project add .

# List them
lerim project list

# Remove one
lerim project remove frontend
```

## Notes

- Adding or removing a project restarts the Docker container if it is running (to update volume mounts)
- Each project stores its own memories in `<repo>/.lerim/memory/`
- Add `.lerim/` to your `.gitignore` if you do not want project memory files tracked in git

## Related commands

<div class="grid cards" markdown>

-   :material-play-circle: **lerim init**

    ---

    Interactive setup wizard

    [:octicons-arrow-right-24: lerim init](init.md)

-   :material-docker: **lerim up**

    ---

    Start the Docker service

    [:octicons-arrow-right-24: lerim up](up-down-logs.md)

</div>
