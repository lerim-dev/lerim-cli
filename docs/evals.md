# Evaluation Framework

Last updated: 2026-03-01

## Overview

Lerim includes an evaluation framework for measuring the quality of its extraction and summarization pipelines. The framework combines deterministic checks (schema validation, field presence, word limits) with LLM-as-judge scoring using coding agent CLIs.

Key design choice: **no new dependencies or API keys required**. LLM judge scoring uses coding agent CLIs (Claude Code, Codex, OpenCode) that developers already have installed. Users leverage their existing coding agent subscriptions instead of paying for a separate eval service.

All eval code lives in `evals/` at the repository root.

## Quick start

```bash
# 1. Place session trace files in evals/traces/
cp path/to/session.jsonl evals/traces/

# 2. Run extraction eval
python evals/run_extraction.py

# 3. Run summarization eval
python evals/run_summarization.py

# 4. Compare results across runs
python evals/compare.py
```

## Directory structure

```
evals/
  eval_config.toml          # Default eval config (judge agent, pipeline models)
  scores.py                 # EvalScore dataclass + deterministic checks
  judge.py                  # Coding agent CLI judge wrapper
  run_extraction.py         # Extraction eval runner
  run_summarization.py      # Summarization eval runner
  compare.py                # Cross-run comparison table
  judge_prompts/            # Prompt templates
    extraction.md           # Extraction quality judge prompt
    summarization.md        # Summarization quality judge prompt
    golden_extraction.md    # Golden dataset creation prompt (extraction)
    golden_summarization.md # Golden dataset creation prompt (summarization)
  traces/                   # Session trace files (.jsonl/.json)
  results/                  # Eval output JSONs (gitignored)
```

## Configuration

Eval config is a TOML file that controls which judge agent to use and which model to run each pipeline with.

Default config (`evals/eval_config.toml`):

```toml
[judge]
agent = "claude"  # "claude" | "codex" | "opencode"

[extraction]
provider = "minimax"
model = "MiniMax-M2.5"

[summarization]
provider = "minimax"
model = "MiniMax-M2.5"
```

### Comparing models

Create multiple configs and run the same eval with each:

```bash
# Create configs for different models
cp evals/eval_config.toml evals/eval_cheap.toml
cp evals/eval_config.toml evals/eval_premium.toml
# Edit each to change the model

# Run evals with each config
python evals/run_extraction.py --config evals/eval_cheap.toml
python evals/run_extraction.py --config evals/eval_premium.toml

# Compare all results side by side
python evals/compare.py
python evals/compare.py --pipeline extraction
```

## Scoring

Each trace is scored on two layers: deterministic checks and LLM judge quality scores.

### Deterministic checks

These are fast, free, and deterministic. They run before the judge.

**Extraction checks:**

| Check | What it validates |
|-------|-------------------|
| `schema_ok` | Each extracted item validates against the `MemoryCandidate` Pydantic schema |
| `has_candidates` | Pipeline produced at least one candidate |

**Summarization checks:**

| Check | What it validates |
|-------|-------------------|
| `fields_present` | Required frontmatter fields (`title`, `description`, `user_intent`, `session_narrative`, `coding_agent`) are all non-empty |
| `word_limits` | `user_intent` <= 150 words, `session_narrative` <= 200 words |

### LLM judge scoring

The judge is a coding agent CLI invoked via `subprocess.run()`. It reads the original trace file, examines the pipeline output, and scores on three dimensions:

| Dimension | Weight | Description |
|-----------|--------|-------------|
| completeness | 40% | Did the pipeline capture all important signals from the trace? |
| faithfulness | 35% | Are all items grounded in the trace with no hallucinations? |
| clarity | 25% | Are the items well-written, concise, with good titles? |

**Composite score** = completeness * 0.4 + faithfulness * 0.35 + clarity * 0.25

Each dimension is scored 0.0 to 1.0. The judge returns JSON with scores and a reasoning field.

### Ship thresholds

| Pipeline | Composite threshold |
|----------|---------------------|
| Extraction | >= 70/100 |
| Summarization | >= 65/100 |

## How the judge works

The judge wrapper (`evals/judge.py`) supports three coding agent CLIs:

| Agent | Command |
|-------|---------|
| `claude` | `claude -p <prompt> --output-format json --allowedTools Read` |
| `codex` | `codex exec <prompt> --json --ephemeral` |
| `opencode` | `opencode run <prompt> --format json` |

The prompt is assembled from a template in `judge_prompts/` with the trace file path and pipeline output injected. The judge agent reads the original trace, compares it to the pipeline output, and returns structured JSON scores.

The wrapper handles JSON parsing from each agent's output format, including extracting JSON from markdown code blocks when agents wrap their output.

## Result format

Results are saved as JSON in `evals/results/` (gitignored). Each file is timestamped:

```
evals/results/extraction_20260228_143000.json
evals/results/summarization_20260228_143500.json
```

Result structure:

```json
{
  "timestamp": "2026-02-28T14:30:00Z",
  "pipeline": "extraction",
  "config": {"provider": "minimax", "model": "MiniMax-M2.5"},
  "judge": {"agent": "claude", "model": ""},
  "performance": {
    "total_wall_time_s": 45.2,
    "avg_time_per_trace_s": 15.1,
    "trace_count": 3
  },
  "scores": {
    "schema_ok": 1.0,
    "completeness": 0.75,
    "faithfulness": 0.82,
    "clarity": 0.68,
    "composite": 0.757
  },
  "per_trace": [...]
}
```

## Comparison table

`evals/compare.py` reads all result files, groups by pipeline, and prints a side-by-side table:

```
Pipeline: extraction
Config                                   schema  compl  faith   clar   COMP  time/t
--------------------------------------------------------------------------------
MiniMax-M2.5 (minimax)                      1.00   0.75   0.82   0.68   0.76   15.1s
claude-sonnet (anthropic)                   1.00   0.88   0.91   0.80   0.87   22.3s
```

Filter by pipeline:

```bash
python evals/compare.py --pipeline extraction
python evals/compare.py --pipeline summarization
```

## Golden datasets

Judge prompt templates for creating gold-standard ground truth data are in `judge_prompts/`:

- `golden_extraction.md` — prompt for producing gold-standard memory extraction from a trace
- `golden_summarization.md` — prompt for producing gold-standard session summary from a trace

Use them with any coding agent to create ground-truth data for more rigorous eval comparison:

```bash
# Example: use Claude Code to generate a golden extraction
claude -p "$(cat evals/judge_prompts/golden_extraction.md | sed 's|{trace_path}|evals/traces/session1.jsonl|')" \
  --output-format json --allowedTools Read
```

Golden datasets are the foundation for prompt optimization (DSPy MIPROv2), supervised fine-tuning, and RL training in later phases.

## Adding traces

Place `.jsonl` or `.json` session trace files in `evals/traces/`. These should be real coding agent session transcripts. Both eval runners automatically discover all trace files in this directory.

## Source files

| File | Purpose |
|------|---------|
| `evals/scores.py` | `EvalScore` dataclass, `compute_composite()`, deterministic check functions |
| `evals/judge.py` | `invoke_judge()` subprocess wrapper, `build_judge_prompt()` template injection, output parsing |
| `evals/run_extraction.py` | Extraction eval runner — configures DSPy, runs pipeline, scores, saves results |
| `evals/run_summarization.py` | Summarization eval runner — same flow for summarization pipeline |
| `evals/compare.py` | Loads all result JSONs, groups by pipeline, prints comparison table |
