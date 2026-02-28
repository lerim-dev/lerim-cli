# Lerim Evals

Evaluation framework for lerim's extraction and summarization pipelines.
Uses coding agent CLIs (claude, codex, opencode) as LLM judges alongside
deterministic schema and content checks.

## Quick Start

```bash
# Run extraction eval with default config
python evals/run_extraction.py

# Run summarization eval
python evals/run_summarization.py

# Compare results across runs
python evals/compare.py
python evals/compare.py --pipeline extraction
```

## Adding Traces

Place `.jsonl` or `.json` session trace files in `evals/traces/`.
These are real coding-agent session transcripts used as eval inputs.

## Configuration

Default config: `evals/eval_config.toml`

Create multiple configs to compare models:

```bash
cp evals/eval_config.toml evals/eval_cheap.toml
# Edit eval_cheap.toml to use a cheaper model
python evals/run_extraction.py --config evals/eval_cheap.toml

cp evals/eval_config.toml evals/eval_premium.toml
# Edit eval_premium.toml to use a premium model
python evals/run_extraction.py --config evals/eval_premium.toml

# Compare all results
python evals/compare.py
```

Config sections:

- `[judge]` -- which coding agent CLI to use as judge (`claude`, `codex`, `opencode`)
- `[extraction]` -- provider/model for the extraction pipeline
- `[summarization]` -- provider/model for the summarization pipeline

## Scoring Dimensions

Each trace is scored on:

| Dimension       | Weight | Description                                    |
|-----------------|--------|------------------------------------------------|
| completeness    | 40%    | Did it capture all important signals?          |
| faithfulness    | 35%    | Are all items grounded in the trace?           |
| clarity         | 25%    | Are items well-written and concise?            |

**Composite** = completeness * 0.4 + faithfulness * 0.35 + clarity * 0.25

### Deterministic Checks

**Extraction**: `schema_ok` (validates against MemoryCandidate), `has_candidates`

**Summarization**: `fields_present` (required frontmatter fields), `word_limits` (user_intent <= 150 words, narrative <= 200 words)

## Result Format

Results are saved as JSON in `evals/results/` (gitignored):

```
evals/results/extraction_20260228_143000.json
evals/results/summarization_20260228_143500.json
```

## Golden Datasets (Future)

Judge prompts for generating gold-standard outputs are in:

- `judge_prompts/golden_extraction.md`
- `judge_prompts/golden_summarization.md`

These can be used with any coding agent to produce ground-truth data
for more rigorous eval comparison.

## Directory Structure

```
evals/
  eval_config.toml      # Default eval config
  traces/               # Session trace files (.jsonl/.json)
  judge_prompts/        # LLM judge prompt templates
  results/              # Eval output JSONs (gitignored)
  scores.py             # EvalScore dataclass + deterministic checks
  judge.py              # Coding agent CLI judge wrapper
  run_extraction.py     # Extraction eval runner
  run_summarization.py  # Summarization eval runner
  compare.py            # Cross-run comparison table
```
