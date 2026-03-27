# lerim up / down / logs

Docker container lifecycle commands for starting, stopping, and monitoring Lerim.

## Overview

These host-only commands manage the Docker container that runs `lerim serve` (daemon + JSON API).

!!! info "Host-only commands"
    These commands run on the host machine. They do not require a running Lerim server.

## Syntax

```bash
lerim up [--build]
lerim down
lerim logs [--follow]
```

## Commands

### `lerim up`

Start Lerim as a Docker service:

```bash
lerim up                    # start Lerim (pull GHCR image)
lerim up --build            # build from local Dockerfile instead
```

This reads `~/.lerim/config.toml`, generates a `docker-compose.yml` in `~/.lerim/`, and runs `docker compose up -d`.

By default the compose file references the pre-built GHCR image (`ghcr.io/lerim-dev/lerim-cli`) tagged with the current package version. Use `--build` to build from the local Dockerfile instead (useful for development).

Running `lerim up` again recreates the container.

### `lerim down`

Stop the Docker container:

```bash
lerim down
```

### `lerim logs`

View container logs:

```bash
lerim logs                  # show recent logs
lerim logs --follow         # tail logs continuously
```

## Parameters

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--build</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Build from local Dockerfile instead of pulling the GHCR image.</p>
</div>

<div class="param-field">
  <div class="param-header">
    <span class="param-name">--follow</span>
    <span class="param-type">boolean</span>
    <span class="param-badge default">default: off</span>
  </div>
  <p class="param-desc">Continuously tail logs (for <code>lerim logs</code>).</p>
</div>

## Examples

```bash
# Start the service
lerim up

# Check it's running
lerim status

# View logs
lerim logs --follow

# Stop when done
lerim down
```

## Notes

- The container runs `lerim serve` which provides the daemon loop and JSON API (web UI: [Lerim Cloud](https://lerim.dev))
- Dashboard is available at `http://localhost:8765` when running
- Docker restart policy is `"no"` — the container does not auto-restart after reboots

## Related commands

<div class="grid cards" markdown>

-   :material-server: **lerim serve**

    ---

    Run directly without Docker

    [:octicons-arrow-right-24: lerim serve](serve.md)

-   :material-chart-box: **lerim status**

    ---

    Check runtime state

    [:octicons-arrow-right-24: lerim status](status.md)

</div>
