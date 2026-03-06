#!/usr/bin/env bash
# Benchmark Lerim pipelines across multiple model configs.
#
# Runs extraction and summarization evals for each config, then prints
# a comparison table. Results accumulate in evals/results/.
#
# Usage:
#   ./evals/scripts/bench_models.sh                              # all configs in evals/configs/
#   ./evals/scripts/bench_models.sh evals/configs/eval_minimax_m25.toml evals/configs/eval_ollama_4b_q8_think_off.toml
#
# Environment variables:
#   TRACES_DIR    Override traces directory (default: evals/dataset/traces/ if exists, else evals/traces/)
#   LIMIT         Max traces per eval run (default: 5)
#   PIPELINES     Space-separated pipelines to run (default: "extraction summarization")
#   CLEAN         Set to 1 to clear evals/results/ before running
#   JUDGE_TIMEOUT Override judge timeout in seconds (overrides config value)

set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

# --- Configuration -----------------------------------------------------------

LIMIT="${LIMIT:-5}"
PIPELINES="${PIPELINES:-extraction summarization}"
CLEAN="${CLEAN:-0}"

# Auto-detect traces directory
if [[ -n "${TRACES_DIR:-}" ]]; then
    traces_dir="$TRACES_DIR"
elif [[ -d evals/dataset/traces ]] && [[ -n "$(ls evals/dataset/traces/*.jsonl 2>/dev/null)" ]]; then
    traces_dir="evals/dataset/traces"
else
    traces_dir="evals/traces"
fi

# Collect configs: args or all in evals/configs/
if [[ $# -gt 0 ]]; then
    configs=("$@")
else
    configs=(evals/configs/*.toml)
fi

# --- Pre-flight checks -------------------------------------------------------

echo "=== Lerim Model Benchmark ==="
echo "Traces:    $traces_dir"
echo "Limit:     $LIMIT traces per run"
echo "Pipelines: $PIPELINES"
echo "Configs:   ${#configs[@]}"
for c in "${configs[@]}"; do echo "  - $c"; done
echo ""

if [[ "$CLEAN" == "1" ]]; then
    echo "Cleaning evals/results/..."
    rm -f evals/results/*.json
    echo ""
fi

# Verify traces exist
trace_count=$(find "$traces_dir" -maxdepth 1 \( -name "*.jsonl" -o -name "*.json" \) 2>/dev/null | wc -l | tr -d ' ')
if [[ "$trace_count" == "0" ]]; then
    echo "ERROR: No trace files found in $traces_dir"
    echo "Run the dataset pipeline first: PYTHONPATH=. python evals/dataset/build.py --agent claude"
    exit 1
fi
echo "Found $trace_count trace files"
echo ""

# --- Run evals ---------------------------------------------------------------

total_configs=${#configs[@]}
config_idx=0

for config in "${configs[@]}"; do
    config_idx=$((config_idx + 1))
    config_name=$(basename "$config" .toml)
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "[$config_idx/$total_configs] $config_name"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    for pipeline in $PIPELINES; do
        echo ""
        echo "--- $pipeline ---"
        runner="evals/run_${pipeline}.py"
        if [[ ! -f "$runner" ]]; then
            echo "  SKIP: runner not found: $runner"
            continue
        fi

        PYTHONPATH=. python3 "$runner" \
            --config "$config" \
            --traces-dir "$traces_dir" \
            --limit "$LIMIT" \
            2>&1 | while IFS= read -r line; do echo "  $line"; done

        echo ""
    done
done

# --- Comparison table ---------------------------------------------------------

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "COMPARISON TABLE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

for pipeline in $PIPELINES; do
    PYTHONPATH=. python3 evals/compare.py --pipeline "$pipeline" 2>&1
done

echo ""
echo "=== Benchmark complete. Results in evals/results/ ==="
