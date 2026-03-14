# Lerim Evals

Evaluation framework for Lerim's core pipelines. Combines deterministic schema
checks with LLM-as-judge scoring using coding agent CLIs (Claude Code, Codex,
or OpenCode). No extra dependencies or API keys needed for judging.

All eval runners are isolated -- they never read from or write to `~/.lerim/`
or `<repo>/.lerim/`.

## Pipelines

| Pipeline | Runner | What it tests |
|----------|--------|---------------|
| Extraction | `run_extraction.py` | DSPy memory candidate extraction from session traces |
| Summarization | `run_summarization.py` | DSPy structured session summary generation |
| Lifecycle | `run_lifecycle.py` | Full sync + maintain flow (accumulation, dedup, merge, archive) |

## Quick start

```bash
# Run any pipeline eval (--config is required)
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_summarization.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_lifecycle.py --config evals/configs/eval_minimax_m25.toml --limit 5 --maintain-every 3

# Compare results across runs
PYTHONPATH=. python evals/compare.py
```

## Configuration

Each eval requires a TOML config (`--config`). Configs live in `evals/configs/`.
Copy an existing one and change the model:

```bash
cp evals/configs/eval_minimax_m25.toml evals/configs/eval_my_model.toml
```

Supported providers: `ollama`, `minimax`, `zai`, `openrouter`, `openai`, `mlx`.

## Traces

| Directory | Contents | Git-tracked? |
|-----------|----------|--------------|
| `evals/traces/` | Synthetic smoke-test traces | Yes |
| `evals/dataset/traces/` | Real traces from dataset pipeline | No (gitignored) |

All runners accept `--traces-dir` to override the default directory.

## Multi-model benchmark

```bash
./evals/scripts/bench_models.sh                    # all configs
./evals/scripts/bench_models.sh evals/configs/eval_minimax_m25.toml  # specific config
LIMIT=3 PIPELINES=extraction ./evals/scripts/bench_models.sh         # customize
```

## Dataset pipeline

Build a benchmark dataset from your real coding sessions:

```bash
PYTHONPATH=. python evals/dataset/build.py --agent claude
```

Configure in `evals/dataset/config.toml`.

## Key files

| File | Purpose |
|------|---------|
| `scores.py` | Deterministic checks and composite score computation |
| `judge.py` | Coding agent CLI judge wrapper |
| `compare.py` | Cross-run comparison table |
| `scripts/bench_models.sh` | Multi-model benchmark runner |
| `dataset/build.py` | Dataset pipeline entry point |
