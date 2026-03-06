# Lerim Evals

Evaluation framework for Lerim's pipelines: extraction, summarization,
and lifecycle (full agentic sync + maintain). Combines deterministic
schema checks with LLM-as-judge scoring using coding agent CLIs.

All eval runners are isolated — they never read from or write to
`~/.lerim/` or `<repo>/.lerim/`.

See [docs/evals.md](../docs/evals.md) for the full reference.

## Quick start

```bash
# Run extraction eval (--config is required)
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml

# Run with a local model config
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_ollama_9b_q8.toml

# Run lifecycle eval (sequential syncs + periodic maintains)
PYTHONPATH=. python evals/run_lifecycle.py --config evals/configs/eval_minimax_m25.toml --limit 5 --maintain-every 3

# Compare results across runs
PYTHONPATH=. python evals/compare.py
PYTHONPATH=. python evals/compare.py --pipeline lifecycle
```

## Directory structure

```
evals/
  configs/                  # Model-specific eval configs (--config is required)
    eval_ollama_4b_q8_think_off.toml
    eval_ollama_4b_q8_think_on.toml
    eval_ollama_9b_q4.toml
    eval_ollama_9b_q8.toml
    eval_ollama_35b_q4.toml
    eval_minimax_m25.toml
  run_extraction.py         # Extraction pipeline eval runner
  run_summarization.py      # Summarization pipeline eval runner
  run_lifecycle.py          # Lifecycle eval runner (sequential syncs + periodic maintains)
  scores.py                 # EvalScore dataclass + deterministic checks
  judge.py                  # Coding agent CLI judge wrapper
  compare.py                # Cross-run comparison table
  judge_prompts/            # LLM judge prompt templates
  traces/                   # Synthetic smoke-test traces (git-tracked, ships with repo)
  results/                  # Eval output JSONs (gitignored)
  scripts/                  # Standalone benchmark utilities
    bench_models.sh         # Multi-model benchmark with comparison
```

### Trace directories

| Directory | Contents | Git-tracked? | Default? |
|-----------|----------|--------------|----------|
| `evals/traces/` | 3 synthetic smoke-test traces | Yes | Yes |
| `evals/dataset/traces/` | Real traces from your coding-agent sessions | No (gitignored) | No — use `--traces-dir` |

## Multi-model benchmark

`bench_models.sh` runs extraction, summarization, and lifecycle evals across
all configs (or a subset), then prints a comparison table:

```bash
# Run all configs in evals/configs/
./evals/scripts/bench_models.sh

# Run specific configs
./evals/scripts/bench_models.sh evals/configs/eval_minimax_m25.toml evals/configs/eval_ollama_9b_q8.toml

# Customize: 3 traces, only extraction, use real dataset, clean results first
LIMIT=3 PIPELINES=extraction TRACES_DIR=evals/dataset/traces CLEAN=1 ./evals/scripts/bench_models.sh
```

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `TRACES_DIR` | auto (`evals/dataset/traces/` if exists, else `evals/traces/`) | Traces directory |
| `LIMIT` | `5` | Max traces per eval run |
| `PIPELINES` | `extraction summarization lifecycle` | Space-separated pipelines to run |
| `CLEAN` | `0` | Set to `1` to clear `evals/results/` before running |

## Dataset pipeline

Build a personalized eval benchmark dataset from your real coding-agent session traces.

```bash
# Build dataset (scans connected platforms, assesses quality, selects diverse traces)
PYTHONPATH=. python evals/dataset/build.py --agent claude

# Run evals against the dataset
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml --traces-dir evals/dataset/traces/
PYTHONPATH=. python evals/run_lifecycle.py --config evals/configs/eval_minimax_m25.toml --traces-dir evals/dataset/traces/ --limit 10
```

The pipeline scans sessions from platforms configured in `evals/dataset/config.toml`,
uses a coding agent CLI to assess quality and label topics, then selects diverse traces
for benchmarking.

### Structure

```
evals/dataset/
  build.py                  # Pipeline entry point
  config.toml               # Pipeline config (platforms, diversity targets, quality thresholds)
  catalog.json              # All candidates with assessments (gitignored)
  manifest.json             # Selected traces metadata (gitignored)
  traces/                   # Exported trace files (gitignored)
```

### Configuration

Edit `evals/dataset/config.toml` to configure which platforms to scan, diversity
targets, and quality thresholds. Each `[[sources]]` entry specifies a platform
(`claude`, `codex`, `opencode`, `cursor`) and path to scan for session files.

### `--traces-dir` flag

All eval runners accept `--traces-dir` to override the default `evals/traces/` directory:

```bash
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml --traces-dir evals/dataset/traces/
```

See [docs/evals.md](../docs/evals.md) for the full dataset pipeline reference.
