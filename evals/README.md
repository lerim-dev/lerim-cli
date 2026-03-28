# Lerim Evals

Evaluation framework for Lerim's core pipelines. Combines deterministic schema
checks with LLM-as-judge scoring using coding agent CLIs (Claude Code, Codex,
or OpenCode). No extra dependencies or API keys needed for judging.

All eval runners are isolated -- they never read from or write to `~/.lerim/`
or `<repo>/.lerim/`.

## Pipelines

| Pipeline | Runner | What it tests | Scoring |
|----------|--------|---------------|---------|
| Extraction | `run_extraction.py` | DSPy memory candidate extraction from session traces | Judge (4-dim) |
| Summarization | `run_summarization.py` | DSPy structured session summary generation | Judge (4-dim) |
| Lifecycle | `run_lifecycle.py` | Full sync + maintain flow (accumulation, dedup, merge, archive) | Judge (4-dim) |
| Dedup | `run_dedup.py` | Dedup classification accuracy against golden assertions | Deterministic + Judge |
| Maintain | `run_maintain.py` | Isolated maintain quality (archive/merge precision) | Deterministic + Judge |
| Search | `run_search.py` | Search relevance via NDCG@5 | Deterministic (NDCG) |
| Tool Selection | `run_tool_selection.py` | Tool call sequence accuracy from agent traces | Deterministic |

## Quick start

```bash
# Trace-based evals (--config is required)
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_summarization.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_lifecycle.py --config evals/configs/eval_minimax_m25.toml --limit 5 --maintain-every 3

# Golden dataset evals (--golden-dir is required)
PYTHONPATH=. python evals/run_dedup.py --config evals/configs/eval_minimax_m25.toml --golden-dir path/to/golden/dedup/
PYTHONPATH=. python evals/run_maintain.py --config evals/configs/eval_minimax_m25.toml --golden-dir path/to/golden/maintain/
PYTHONPATH=. python evals/run_search.py --golden-dir path/to/golden/search/
PYTHONPATH=. python evals/run_tool_selection.py --golden-dir path/to/golden/tool_selection/

# Compare results across runs
PYTHONPATH=. python evals/compare.py
```

## Golden Dataset

The `--golden-dir` flag points to a directory of golden test cases. Each case is a
subdirectory with an `input/` and `expected/` split:

```
golden/dedup/
  case_001/
    input/
      trace.jsonl          # session trace
      memory_store/        # pre-populated memories
        decisions/
        learnings/
    expected/
      assertions.json      # golden assertions
  case_002/
    ...

golden/maintain/
  case_001/
    input/
      memory_store/        # pre-populated memories
    expected/
      assertions.json      # {"should_archive": [...], "should_merge": [[...]], "should_keep": [...]}

golden/search/
  case_001/
    input/
      memory_store/        # memories to index
      queries.json         # [{"query": "...", "relevant_memory_ids": ["id1", "id2"]}]

golden/tool_selection/
  case_001/
    input/
      agent_trace.json     # OAI agent trace with tool calls
    expected/
      assertions.json      # {"expected_sequence": [...], "must_not_call": [...]}
```

Golden datasets are not checked into git. Build them from real runs or create them
manually for regression testing.

## LerimBench (7 dimensions)

The `scores.py` module provides `LerimBenchScore` and `compute_lerim_bench_composite()`
for computing a weighted composite across all 7 evaluation dimensions:

| Dimension | Weight | Source |
|-----------|--------|--------|
| extraction_precision | 0.20 | `run_extraction.py` judge |
| extraction_recall | 0.20 | `run_extraction.py` judge |
| dedup_accuracy | 0.15 | `run_dedup.py` deterministic |
| consolidation_quality | 0.15 | `run_maintain.py` judge |
| archive_precision | 0.10 | `run_maintain.py` deterministic |
| search_relevance | 0.15 | `run_search.py` NDCG@5 |
| scale_degradation | 0.05 | Lifecycle regression ratio |

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
| `scores.py` | Deterministic checks, LerimBench composite, NDCG, dedup/archive accuracy |
| `judge.py` | Coding agent CLI judge wrapper |
| `compare.py` | Cross-run comparison table |
| `run_dedup.py` | Dedup accuracy eval against golden datasets |
| `run_maintain.py` | Isolated maintain eval against golden datasets |
| `run_search.py` | Search relevance eval (NDCG@5) against golden datasets |
| `run_tool_selection.py` | Tool selection accuracy from agent traces |
| `scripts/bench_models.sh` | Multi-model benchmark runner |
| `dataset/build.py` | Dataset pipeline entry point |
