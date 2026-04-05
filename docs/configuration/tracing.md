# Tracing

Lerim uses [MLflow](https://mlflow.org) for DSPy agent observability.
Tracing is opt-in and controlled by the `LERIM_MLFLOW` environment variable.

## What gets traced

When tracing is enabled, MLflow records:

- **DSPy LM calls** -- via `mlflow.dspy.autolog()`, every language model invocation
  across ReAct agents (sync, maintain, ask) is captured automatically, including
  input prompts, outputs, token counts, and latency.
- **Module executions** -- DSPy module calls, tool invocations, and ReAct
  reasoning steps are traced as nested spans within each run.
- **agent_trace.json** -- each sync/maintain run also writes a local
  `agent_trace.json` under the run workspace for a full tool/message history
  (not MLflow-specific).

## Setup

MLflow is already included with lerim -- no extra install step required:

```bash
pip install mlflow
```

!!! info "No account needed"
	MLflow runs a local tracking server by default. No authentication,
	no cloud account, and no API keys required. Everything stays on your machine.

## Enable tracing

Set `LERIM_MLFLOW=true` in your environment or `.env` file:

=== "Environment variable"

	Quick toggle for a single command:

	```bash
	LERIM_MLFLOW=true lerim sync
	LERIM_MLFLOW=true lerim ask "Why did we choose Postgres?"
	```

=== ".env file"

	Persistent toggle in `~/.lerim/.env` or `<repo>/.lerim/.env`:

	```bash
	LERIM_MLFLOW=true
	```

## Viewing traces

Start the MLflow UI and open your browser:

```bash
mlflow ui
```

Then navigate to [http://localhost:5000](http://localhost:5000). You'll see:

- **Runs** -- each sync or maintain cycle appears as a separate run with
  parameters, metrics, and artifacts.
- **Traces** -- expand a run to see the full trace tree of DSPy calls.
- **DSPy calls** -- every `dspy.LM` invocation is logged with input prompts,
  outputs, token counts, and latency.
- **Spans** -- nested spans show the call hierarchy from the top-level
  orchestration down to individual LM calls and tool invocations.

!!! tip "Filtering"
	Use the MLflow search bar to filter runs by experiment name, tags, or
	parameters. This is useful when you have many sync/maintain cycles logged.

## Optional configuration

For advanced setups you can point MLflow at a remote tracking server or
customize the experiment name:

| Variable | Default | Description |
|----------|---------|-------------|
| `MLFLOW_TRACKING_URI` | `mlruns` (local directory) | URI of the MLflow tracking server. Set to a remote URL to centralize traces. |
| `MLFLOW_EXPERIMENT_NAME` | `lerim` | Experiment name under which runs are grouped in the MLflow UI. |

```bash
# Example: remote MLflow server
export MLFLOW_TRACKING_URI=http://mlflow.example.com:5000
export MLFLOW_EXPERIMENT_NAME=lerim-prod
LERIM_MLFLOW=true lerim sync
```

!!! tip "Local is the default"
	When no `MLFLOW_TRACKING_URI` is set, MLflow writes to a local `mlruns/`
	directory in the current working directory. Run `mlflow ui` from that
	directory to browse traces.
