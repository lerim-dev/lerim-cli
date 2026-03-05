# Lerim Evals

Evaluation framework for Lerim's four pipelines: extraction, summarization,
sync (full agentic), and maintain (memory maintenance). Combines deterministic
schema checks with LLM-as-judge scoring using coding agent CLIs.

See [docs/evals.md](../docs/evals.md) for the full reference.

## Quick start

```bash
# Run extraction eval with default config
PYTHONPATH=. python evals/run_extraction.py

# Run with a specific model config
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_ollama_9b_q8.toml

# Run sync/maintain with trace limit
PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_ollama_9b_q8.toml --limit 1
PYTHONPATH=. python evals/run_maintain.py --config evals/configs/eval_minimax_m25.toml --limit 1

# Compare results across runs
PYTHONPATH=. python evals/compare.py
PYTHONPATH=. python evals/compare.py --pipeline extraction
```

## Directory structure

```
evals/
  eval_config.toml          # Default config (copy to configs/ to customize)
  configs/                  # Model-specific eval configs
    eval_ollama_4b_q8_think_off.toml
    eval_ollama_4b_q8_think_on.toml
    eval_ollama_9b_q4.toml
    eval_ollama_9b_q8.toml
    eval_minimax_m25.toml
  run_extraction.py         # Extraction pipeline eval runner
  run_summarization.py      # Summarization pipeline eval runner
  run_sync.py               # Full agentic sync eval runner
  run_maintain.py           # Memory maintenance eval runner
  scores.py                 # EvalScore dataclass + deterministic checks
  judge.py                  # Coding agent CLI judge wrapper
  compare.py                # Cross-run comparison table
  judge_prompts/            # LLM judge prompt templates
  traces/                   # Session trace files (.jsonl/.json)
  results/                  # Eval output JSONs (gitignored)
  scripts/                  # Standalone benchmark utilities
    bench_ollama.sh         # Compare tok/s and memory across Ollama models
```

## Benchmarking local models

Before running pipeline evals, use `scripts/bench_ollama.sh` to compare raw
inference speed and memory usage across Ollama models:

```bash
# Default model set
./evals/scripts/bench_ollama.sh

# Specific models, thinking off, 5 runs
THINKING=off NUM_RUNS=5 ./evals/scripts/bench_ollama.sh qwen3.5:4b-q8_0 qwen3.5:9b-q8_0
```
