# Evaluation Framework

Last updated: 2026-03-05

## Overview

Lerim includes an evaluation framework for measuring the quality of its four core pipelines:

| Pipeline | What it does | Runner |
|----------|-------------|--------|
| **Extraction** | Extracts memory candidates (decisions, learnings) from a coding session trace using DSPy | `run_extraction.py` |
| **Summarization** | Produces a structured session summary from a trace using DSPy | `run_summarization.py` |
| **Sync** | Full agentic pipeline — orchestrates extraction, summarization, dedup, and memory writes using PydanticAI + DSPy | `run_sync.py` |
| **Maintain** | Seeds memories via sync, then runs maintenance (merge, archive, consolidate) using PydanticAI + DSPy | `run_maintain.py` |

Each eval combines two scoring layers:

1. **Deterministic checks** — schema validation, field presence, word limits (fast, free, reproducible)
2. **LLM-as-judge scoring** — a coding agent CLI (Claude Code, Codex, or OpenCode) reads the original trace and the pipeline output, then scores quality on three dimensions

Key design choice: **no new dependencies or API keys required for judging**. The judge uses coding agent CLIs that developers already have installed, leveraging existing subscriptions.

## Quick start

```bash
# 1. Place session trace files in evals/traces/
cp path/to/session.jsonl evals/traces/

# 2. Run any pipeline eval (--config is required)
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_summarization.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/run_maintain.py --config evals/configs/eval_minimax_m25.toml

# 3. Limit traces for faster runs
PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_ollama_9b_q8.toml --limit 1

# 4. Compare results across runs
PYTHONPATH=. python evals/compare.py
PYTHONPATH=. python evals/compare.py --pipeline sync
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
  run_sync.py               # Full agentic sync eval runner
  run_maintain.py           # Memory maintenance eval runner
  scores.py                 # EvalScore dataclass + deterministic checks
  judge.py                  # Coding agent CLI judge wrapper
  compare.py                # Cross-run comparison table
  judge_prompts/            # LLM judge prompt templates
    extraction.md           # Extraction quality judge prompt
    summarization.md        # Summarization quality judge prompt
    sync.md                 # Sync quality judge prompt
    maintain.md             # Maintain quality judge prompt
    golden_extraction.md    # Golden dataset creation prompt
    golden_summarization.md # Golden dataset creation prompt
  traces/                   # Synthetic smoke-test traces (git-tracked, ships with repo)
  results/                  # Eval output JSONs (gitignored)
  scripts/                  # Standalone benchmark utilities
    bench_ollama.sh         # Compare tok/s and memory across Ollama models
    bench_models.sh         # Multi-model benchmark with comparison
  dataset/                  # Dataset pipeline (real traces, gitignored output)
    build.py                # Pipeline entry point
    config.toml             # Pipeline config
    traces/                 # Real traces built by pipeline (gitignored)
```

### Trace directories

There are two trace directories — one synthetic, one real:

| Directory | Contents | Tracked in git? | Default for runners? |
|-----------|----------|-----------------|----------------------|
| `evals/traces/` | 3 synthetic smoke-test traces that ship with the repo | Yes | Yes |
| `evals/dataset/traces/` | Real session traces built by the dataset pipeline from your coding-agent sessions | No (gitignored) | No — pass `--traces-dir evals/dataset/traces/` |

Use `evals/traces/` for quick smoke tests. Use `evals/dataset/traces/` for real benchmarking.

## Configuration

Each eval run is controlled by a TOML config file. The `--config` flag is **required** — there is no default config.

The config specifies:

- Which **judge agent** scores the output (Claude Code, Codex, or OpenCode)
- Which **provider and model** runs each role (lead, explorer, extraction, summarization)
- Whether **thinking mode** is enabled (relevant for reasoning models like Qwen 3.5)
- **Timeout** in seconds for the judge and each role

### Required sections

Every eval config must contain all 5 sections:

```toml
[judge]
agent = "claude"               # "claude" | "codex" | "opencode"
timeout_seconds = 300

[lead]
provider = "ollama"
model = "qwen3.5:9b-q8_0"
thinking = false
timeout_seconds = 300

[explorer]
provider = "ollama"
model = "qwen3.5:9b-q8_0"
thinking = false
timeout_seconds = 180

[extraction]
provider = "ollama"
model = "qwen3.5:9b-q8_0"
thinking = false
timeout_seconds = 180

[summarization]
provider = "ollama"
model = "qwen3.5:9b-q8_0"
thinking = false
timeout_seconds = 180
```

For single-model benchmarks, use the same provider/model for all 4 roles.

### Creating a new config

Copy an existing config and change the model:

```bash
cp evals/configs/eval_minimax_m25.toml evals/configs/eval_my_model.toml
```

Supported providers: `ollama`, `minimax`, `openrouter`, `anthropic`, `openai`.

### Comparing models

Run the same eval with different configs, then compare:

```bash
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_ollama_9b_q8.toml
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml
PYTHONPATH=. python evals/compare.py --pipeline extraction
```

## Eval data: session traces

Eval inputs are coding agent session traces stored in `evals/traces/`. These are JSON files that capture a complete coding session: user messages, assistant responses, tool calls, and file edits.

Currently included:

| Trace | Agent | Task | Size |
|-------|-------|------|------|
| `claude-coding-run.json` | Claude Code | Add login form validation | ~3.7KB |
| `codex-coding-run.json` | Codex | Implement rate limiting | ~3.7KB |
| `opencode-coding-run.json` | OpenCode | Add retry with backoff | ~3.7KB |

These are synthetic but realistic traces that cover common coding patterns: form validation, API rate limiting, and error retry logic. Each contains user instructions, assistant reasoning, code changes, and test results.

To add your own traces, place `.jsonl` or `.json` files in `evals/traces/`. All runners auto-discover trace files in this directory.

## Scoring

### Deterministic checks

Fast, free, reproducible checks that run before the judge.

**Extraction checks:**

| Check | What it validates |
|-------|-------------------|
| `schema_ok` | Each extracted item validates against the `MemoryCandidate` Pydantic schema |
| `has_candidates` | Pipeline produced at least one memory candidate |

**Summarization checks:**

| Check | What it validates |
|-------|-------------------|
| `fields_present` | Required frontmatter fields (`title`, `description`, `user_intent`, `session_narrative`, `coding_agent`) are all non-empty |
| `word_limits` | `user_intent` <= 150 words, `session_narrative` <= 200 words |

**Sync checks:**

| Check | What it validates |
|-------|-------------------|
| `schema_ok` | All three artifacts (extraction, summary, memory_actions) are present and non-null |
| `has_candidates` | Extraction produced at least one candidate |

**Maintain checks:**

| Check | What it validates |
|-------|-------------------|
| `success` | Maintain pipeline completed without error |
| `memory_files_before/after` | Memory file counts before and after maintenance |

### LLM judge scoring

The judge is a coding agent CLI invoked via subprocess. It reads the original trace, examines the pipeline output, and scores on three dimensions (each 0.0 to 1.0):

| Dimension | Weight | Description |
|-----------|--------|-------------|
| **Completeness** | 40% | Did the pipeline capture all important signals from the trace? Nothing missed? |
| **Faithfulness** | 35% | Are all outputs grounded in the trace? No hallucinated or invented content? |
| **Clarity/Coherence** | 25% | Are outputs well-written, concise, internally consistent? |

**Composite score** = completeness x 0.4 + faithfulness x 0.35 + clarity x 0.25

The third dimension is called "clarity" for extraction/summarization and "coherence" for sync/maintain, but the weight is the same.

### How the judge works

The judge wrapper (`evals/judge.py`) supports three coding agent CLIs:

| Agent | Command |
|-------|---------|
| `claude` | `claude -p <prompt> --output-format json --allowedTools Read` |
| `codex` | `codex exec <prompt> --json --ephemeral` |
| `opencode` | `opencode run <prompt> --format json` |

The prompt is assembled from a template in `judge_prompts/` with the trace path and pipeline output injected. The judge reads the original trace, compares it against the pipeline output, and returns structured JSON with scores and reasoning.

### Ship thresholds

| Pipeline | Composite threshold |
|----------|---------------------|
| Extraction | >= 0.70 |
| Summarization | >= 0.65 |
| Sync | >= 0.60 |
| Maintain | >= 0.60 |

## The four pipeline evals in detail

### Extraction eval (`run_extraction.py`)

Tests the DSPy extraction pipeline in isolation. For each trace:

1. Runs `extract_memories_from_session_file(trace_path)` — produces a list of `MemoryCandidate` dicts
2. Validates schema (`schema_ok`) and checks candidates exist (`has_candidates`)
3. Sends the output + trace path to the judge for quality scoring

```bash
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_ollama_9b_q8.toml
```

### Summarization eval (`run_summarization.py`)

Tests the DSPy summarization pipeline in isolation. For each trace:

1. Runs `summarize_trace_from_session_file(trace_path)` — produces a summary dict with title, description, user_intent, session_narrative, coding_agent
2. Validates field presence and word limits
3. Sends the output + trace path to the judge

```bash
PYTHONPATH=. python evals/run_summarization.py --config evals/configs/eval_ollama_9b_q8.toml
```

### Sync eval (`run_sync.py`)

Tests the full agentic sync pipeline. This is the most important eval — it exercises the entire system end-to-end. For each trace:

1. Creates an isolated temp directory with `memory/` and `workspace/` subdirs
2. Instantiates `LerimAgent` and calls `agent.sync(trace_path, memory_root, workspace_root)`
3. The agent orchestrates: extraction (DSPy) -> summarization (DSPy) -> dedup check -> memory file writes (PydanticAI)
4. Collects all artifacts (extract.json, summary.json, memory_actions.json)
5. Sends the combined artifacts + trace path to the judge

```bash
PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_ollama_9b_q8.toml --limit 1
```

The `--limit` flag restricts how many traces to process (useful for expensive models).

### Maintain eval (`run_maintain.py`)

Tests memory maintenance quality. This eval has two phases:

1. **Seed phase**: syncs traces to populate a temp memory directory (reuses the sync pipeline)
2. **Maintain phase**: runs `agent.maintain(memory_root, workspace_root)` on the populated memories — the agent reviews existing memories and decides what to merge, archive, consolidate, or leave unchanged

The judge scores whether maintenance actions were reasonable.

```bash
PYTHONPATH=. python evals/run_maintain.py --config evals/configs/eval_minimax_m25.toml --limit 1
```

## Result format

Results are saved as JSON in `evals/results/` (gitignored). Each file is timestamped:

```
evals/results/extraction_20260305_141043.json
evals/results/summarization_20260305_142834.json
evals/results/sync_20260305_162754.json
evals/results/maintain_20260305_171326.json
```

Result structure (extraction example):

```json
{
  "timestamp": "2026-03-05T14:10:43Z",
  "pipeline": "extraction",
  "config": {"provider": "minimax", "model": "MiniMax-M2.5"},
  "judge": {"agent": "claude"},
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

`compare.py` reads all result files, groups by pipeline, and prints a side-by-side table:

```bash
PYTHONPATH=. python evals/compare.py
PYTHONPATH=. python evals/compare.py --pipeline extraction
PYTHONPATH=. python evals/compare.py --pipeline sync
```

## Thinking mode and local models

For models that support thinking/reasoning mode (e.g., Qwen 3.5), the `thinking` field in the config controls whether thinking is enabled:

```toml
[extraction]
provider = "ollama"
model = "qwen3.5:4b-q8_0"
thinking = false   # Disable thinking for faster inference
```

When `thinking = false` and `provider = "ollama"`:

- **DSPy pipelines** (extraction, summarization): `reasoning_effort="none"` is passed to the LM, which DSPy/litellm translates to `think: false` on Ollama's native API
- **PydanticAI pipelines** (sync, maintain): Requests are routed through a LiteLLM proxy that translates OpenAI-format calls to Ollama's native `/api/chat` endpoint, which supports `think: false`

The LiteLLM proxy must be running on port 4000 for think-off Ollama configs to work with sync/maintain:

```bash
.venv/bin/litellm --model ollama_chat/qwen3.5:4b-q8_0 --port 4000 --api_base http://localhost:11434
```

Note: In production (`lerim serve` / `lerim up`), Lerim automatically manages
Ollama model loading and unloading via the `auto_unload` lifecycle. Evals bypass
this — they assume Ollama is running and the model is available for the duration
of the eval run.

## Benchmarking local models

Before running pipeline evals with a local model, use `evals/scripts/bench_ollama.sh` to compare raw inference speed and memory usage across Ollama models. This helps pick the right model/quantization for your hardware.

```bash
# Default model set (Qwen 3.5 variants + GLM)
./evals/scripts/bench_ollama.sh

# Specific models, thinking off, 5 averaged runs
THINKING=off NUM_RUNS=5 ./evals/scripts/bench_ollama.sh qwen3.5:4b-q8_0 qwen3.5:9b-q8_0

# Custom prompt
BENCH_PROMPT="Explain quantum computing" THINKING=off ./evals/scripts/bench_ollama.sh
```

The script measures per-model:

- **Gen tok/s** — generation (decode) throughput, including thinking tokens
- **Prompt tok/s** — prompt evaluation (prefill) throughput
- **Memory** — Ollama RSS, system RAM delta from loading, VRAM/GPU offload %
- **Model info** — parameter count, quantization level, context length

Models are unloaded between runs for clean memory baselines. A warm-up run loads the model before timed runs start. See `evals/scripts/README.md` for the full environment variable reference.

## Golden datasets

Judge prompt templates for creating gold-standard ground truth data are in `judge_prompts/`:

- `golden_extraction.md` — prompt for producing gold-standard memory extraction from a trace
- `golden_summarization.md` — prompt for producing gold-standard session summary from a trace

Use them with any coding agent to create ground-truth data:

```bash
claude -p "$(cat evals/judge_prompts/golden_extraction.md | sed 's|{trace_path}|evals/traces/session1.jsonl|')" \
  --output-format json --allowedTools Read
```

Golden datasets are the foundation for prompt optimization (DSPy MIPROv2), supervised fine-tuning, and RL training in later phases.

## Source files

| File | Purpose |
|------|---------|
| `evals/scores.py` | `EvalScore` dataclass, `compute_composite()`, deterministic check functions |
| `evals/judge.py` | `invoke_judge()` subprocess wrapper, `build_judge_prompt()` template injection, output parsing |
| `evals/run_extraction.py` | Extraction eval runner — configures DSPy, runs pipeline, scores, saves results |
| `evals/run_summarization.py` | Summarization eval runner — same flow for summarization pipeline |
| `evals/run_sync.py` | Sync eval runner — full agentic pipeline, isolated temp dirs per trace |
| `evals/run_maintain.py` | Maintain eval runner — seeds memories via sync, then runs maintenance |
| `evals/compare.py` | Loads all result JSONs, groups by pipeline, prints comparison table |

## Dataset pipeline

Build a personalized eval benchmark dataset from your real coding-agent session traces.

### Overview

The dataset pipeline scans sessions from your connected coding platforms, uses an LLM (via coding agent CLI) to assess quality and label topics, selects diverse traces optimized for benchmarking, and exports them for use with the eval runners.

```mermaid
stateDiagram-v2
    [*] --> Scan
    Scan --> Assess: candidates collected
    Assess --> Select: catalog.json written
    Select --> Export: manifest.json written
    Export --> [*]: traces/ ready

    state Scan {
        [*] --> ValidatePlatforms
        ValidatePlatforms --> ScanSources: supported platforms confirmed
        ScanSources --> ParseMetadata: JSONL files found
    }

    state Assess {
        [*] --> BatchSessions
        BatchSessions --> AgentLabel: send to coding agent
        AgentLabel --> WriteCatalog: topic + type + quality scored
    }

    state Select {
        [*] --> FilterQuality: min_messages, min_quality_score
        FilterQuality --> AdaptDiversity: adjust targets to data
        AdaptDiversity --> GreedySelect: optimize diversity + quality
        GreedySelect --> WriteManifest
    }

    state Export {
        [*] --> CopyTraces: platform_NNN.jsonl
        CopyTraces --> VerifyFiles
    }
```

### Quick start

```bash
# Build dataset using Claude as the assessment agent
PYTHONPATH=. python evals/dataset/build.py --agent claude

# Build with custom config and trace count
PYTHONPATH=. python evals/dataset/build.py --agent claude --config evals/dataset/config.toml --count 50

# Run evals against the dataset
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml --traces-dir evals/dataset/traces/
PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_ollama_9b_q8.toml --traces-dir evals/dataset/traces/ --limit 5
```

### Directory structure

```
evals/dataset/
  __init__.py               # Package init
  build.py                  # Pipeline entry point
  config.toml               # Default pipeline config
  catalog.json              # Generated: all candidates with assessments (gitignored)
  manifest.json             # Generated: selected traces metadata (gitignored)
  traces/                   # Generated: copied trace files (gitignored)
    claude_001.jsonl
    codex_001.jsonl
    opencode_001.jsonl
    cursor_001.jsonl
    ...
```

Only `build.py`, `config.toml`, and `__init__.py` are tracked in git. All generated output (`catalog.json`, `manifest.json`, `traces/`) is gitignored and private.

### Configuration

The pipeline is controlled by `evals/dataset/config.toml`:

```toml
[dataset]
trace_count = 50
agent = "claude"               # default coding agent CLI (overridable via --agent)

# Which platforms to scan — add/remove entries for your setup
[[sources]]
platform = "claude"
path = "~/.claude/projects/"

[[sources]]
platform = "codex"
path = "~/.codex/archived_sessions/"

[[sources]]
platform = "opencode"
path = "~/.lerim/cache/opencode/"

[[sources]]
platform = "cursor"
path = "~/.lerim/cache/cursor/"

[diversity]
max_platform_share = 0.6      # No single platform > 60% (ignored if 1 platform)
min_session_types = 4          # Minimum session type variety
short_pct = 0.2                # <20 messages target share
medium_pct = 0.5               # 20-80 messages target share
long_pct = 0.3                 # 80+ messages target share

[quality]
min_messages = 5               # Minimum messages per trace
min_quality_score = 3          # Minimum LLM quality score (1-5)
```

**Platform validation**: Each `[[sources]]` platform must be in the supported set (`claude`, `codex`, `opencode`, `cursor`). Unsupported platforms raise an error. Missing or empty paths are warned and skipped.

**Diversity targets**: All diversity constraints are best-effort and adapt to available data. If only 1 platform is configured, platform balancing is skipped. Session type and length targets are soft goals, not hard requirements.

### Pipeline phases

| Phase | What it does |
|-------|-------------|
| **Scan** | Discovers JSONL session files from each configured `[[sources]]` entry. Parses platform-specific metadata: message count, first user messages, project info. |
| **Assess** | Batches session snippets and sends them to the configured coding agent CLI for topic labeling, session type classification, and quality scoring (1-5). Writes `catalog.json`. |
| **Select** | Filters candidates by `min_messages` and `min_quality_score`, then uses a greedy algorithm to select diverse traces optimizing for platform balance, session type variety, length distribution, and project coverage. Writes `manifest.json`. |
| **Export** | Copies selected trace files to `evals/dataset/traces/` with standardized names (`{platform}_{NNN}.jsonl`). Files are copied as-is, no format conversion. |

### Using `--traces-dir` with eval runners

All four eval runners accept `--traces-dir` to override the default trace directory:

```bash
# Run extraction eval on real dataset traces
PYTHONPATH=. python evals/run_extraction.py --config evals/configs/eval_minimax_m25.toml --traces-dir evals/dataset/traces/

# Run sync eval with limit
PYTHONPATH=. python evals/run_sync.py --config evals/configs/eval_minimax_m25.toml --traces-dir evals/dataset/traces/ --limit 5
```

When `--traces-dir` is not provided, runners use the default `evals/traces/` directory (the synthetic smoke-test traces).

### Manifest format

`manifest.json` describes the selected dataset:

```json
{
  "version": 1,
  "created": "2026-03-06T...",
  "description": "50 real session traces for Lerim eval benchmark",
  "agent_used": "claude",
  "trace_count": 50,
  "diversity_report": {
    "platforms": {"claude": 20, "codex": 8, "opencode": 15, "cursor": 7},
    "session_types": {"feature_implementation": 15, "debugging": 12, ...},
    "length_categories": {"short": 10, "medium": 25, "long": 15},
    "projects": ["lerim-cli", "finetune-test"]
  },
  "traces": [
    {
      "id": "claude_001",
      "file": "traces/claude_001.jsonl",
      "platform": "claude",
      "project": "lerim-cli",
      "session_type": "feature_implementation",
      "message_count": 47,
      "length_category": "medium",
      "topic": "adding memory extraction pipeline",
      "quality_score": 4,
      "source_path": "~/.claude/projects/.../session.jsonl"
    }
  ]
}
```
