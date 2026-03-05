#!/usr/bin/env bash
# bench_ollama.sh — Compare tokens/second across Ollama models
#
# Usage:
#   ./bench_ollama.sh [model1 model2 ...]
#   ./bench_ollama.sh                        # uses defaults below
#   THINKING=on NUM_RUNS=5 ./bench_ollama.sh qwen3.5:4b
#
# Requirements: ollama, jq, curl, bc

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"
PROMPT="${BENCH_PROMPT:-Explain quantum computing.}"
NUM_RUNS="${NUM_RUNS:-3}"  # runs per model (results are averaged)
THINKING="${THINKING:-off}"  # "on" or "off" — controls /think vs /no_think

# Default models if none provided
if [ $# -gt 0 ]; then
  MODELS=("$@")
else
  MODELS=(
    "qwen3.5:2b"
    "qwen3.5:4b"
    "qwen3.5:4b-q8_0"
    "qwen3.5:9b"
    "qwen3.5:9b-q8_0"
    "qwen3.5:35b"
    "glm-4.7-flash:latest"
  )
fi

# ── Helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[0;33m'
BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

die()  { echo -e "${RED}Error: $*${RESET}" >&2; exit 1; }
info() { echo -e "${CYAN}▸${RESET} $*"; }

# Convert bytes to human-readable (GB with 2 decimals)
bytes_to_gb() {
  echo "scale=2; $1 / 1073741824" | bc
}

# Convert nanoseconds to human-readable
ns_to_human() {
  local ns="$1"
  if [ "$ns" -ge 1000000000 ]; then
    echo "$(echo "scale=2; $ns / 1000000000" | bc)s"
  elif [ "$ns" -ge 1000000 ]; then
    echo "$(echo "scale=0; $ns / 1000000" | bc)ms"
  else
    echo "${ns}ns"
  fi
}

check_deps() {
  for cmd in ollama jq curl bc; do
    command -v "$cmd" &>/dev/null || die "'$cmd' not found. Please install it."
  done
  curl -sf "$OLLAMA_HOST/api/tags" >/dev/null 2>&1 || die "Ollama not reachable at $OLLAMA_HOST"
}

# Get system memory usage in bytes: returns "used|available|total"
get_sys_mem() {
  if [ "$(uname)" = "Darwin" ]; then
    local page_size pages_free pages_active pages_speculative pages_inactive pages_wired total
    page_size=$(sysctl -n hw.pagesize 2>/dev/null || echo 16384)
    total=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    # vm_stat gives page counts
    local vmstat
    vmstat=$(vm_stat 2>/dev/null)
    pages_free=$(echo "$vmstat" | awk '/Pages free/ {gsub(/\./,"",$NF); print $NF}')
    pages_inactive=$(echo "$vmstat" | awk '/Pages inactive/ {gsub(/\./,"",$NF); print $NF}')
    pages_active=$(echo "$vmstat" | awk '/Pages active/ {gsub(/\./,"",$NF); print $NF}')
    pages_wired=$(echo "$vmstat" | awk '/Pages wired/ {gsub(/\./,"",$NF); print $NF}')
    pages_speculative=$(echo "$vmstat" | awk '/Pages speculative/ {gsub(/\./,"",$NF); print $NF}')
    local used avail
    used=$(( (pages_active + pages_wired + pages_speculative) * page_size ))
    avail=$(( (pages_free + pages_inactive) * page_size ))
    echo "${used}|${avail}|${total}"
  else
    local total_kb avail_kb used_kb
    total_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
    used_kb=$((total_kb - avail_kb))
    echo "$((used_kb * 1024))|$((avail_kb * 1024))|$((total_kb * 1024))"
  fi
}

# Get Ollama process RSS in bytes
get_ollama_rss() {
  if [ "$(uname)" = "Darwin" ]; then
    # Sum RSS of all ollama_llama_server processes (model runners) + main ollama process
    local rss_kb
    rss_kb=$(ps -eo rss,comm 2>/dev/null | awk '/ollama/ {sum += $1} END {print sum+0}')
    echo $((rss_kb * 1024))
  else
    local rss_kb
    rss_kb=$(ps -eo rss,comm 2>/dev/null | awk '/ollama/ {sum += $1} END {print sum+0}')
    echo $((rss_kb * 1024))
  fi
}

# ── System info ──────────────────────────────────────────────────────
print_system_info() {
  echo -e "${BOLD}System Info${RESET}"

  # macOS
  if [ "$(uname)" = "Darwin" ]; then
    local total_mem_bytes
    total_mem_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
    local total_mem_gb
    total_mem_gb=$(bytes_to_gb "$total_mem_bytes")

    local chip
    chip=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Unknown")

    # GPU cores (Apple Silicon)
    local gpu_cores
    gpu_cores=$(system_profiler SPDisplaysDataType 2>/dev/null | grep "Total Number of Cores" | awk -F': ' '{print $2}' | head -1)

    echo -e "  ${DIM}Chip:       ${chip}${RESET}"
    [ -n "$gpu_cores" ] && echo -e "  ${DIM}GPU cores:  ${gpu_cores}${RESET}"
    echo -e "  ${DIM}RAM:        ${total_mem_gb} GB${RESET}"

    # Current memory pressure
    local mem_pressure
    mem_pressure=$(memory_pressure 2>/dev/null | grep "System-wide memory free percentage" | awk '{print $NF}')
    [ -n "$mem_pressure" ] && echo -e "  ${DIM}Free mem:   ${mem_pressure}${RESET}"
  else
    # Linux
    local total_kb
    total_kb=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')
    local avail_kb
    avail_kb=$(grep MemAvailable /proc/meminfo 2>/dev/null | awk '{print $2}')
    if [ -n "$total_kb" ]; then
      echo -e "  ${DIM}RAM:        $(echo "scale=2; $total_kb / 1048576" | bc) GB${RESET}"
      [ -n "$avail_kb" ] && echo -e "  ${DIM}Available:  $(echo "scale=2; $avail_kb / 1048576" | bc) GB${RESET}"
    fi

    # GPU info via nvidia-smi
    if command -v nvidia-smi &>/dev/null; then
      local gpu_name gpu_mem
      gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
      gpu_mem=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
      [ -n "$gpu_name" ] && echo -e "  ${DIM}GPU:        ${gpu_name} (${gpu_mem})${RESET}"
    fi
  fi
  echo ""
}

# ── Model info from /api/show and /api/ps ────────────────────────────
print_model_info() {
  local model="$1"

  # Static info from /api/show
  local show_resp
  show_resp=$(curl -sf "$OLLAMA_HOST/api/show" \
    -d "$(jq -n --arg m "$model" '{model: $m}')" 2>/dev/null) || return

  local param_size quant family
  param_size=$(echo "$show_resp" | jq -r '.details.parameter_size // "?"')
  quant=$(echo "$show_resp" | jq -r '.details.quantization_level // "?"')
  family=$(echo "$show_resp" | jq -r '.details.family // "?"')
  local ctx_len
  ctx_len=$(echo "$show_resp" | jq -r '[.model_info | to_entries[] | select(.key | test("context_length")) | .value] | first // "?"')

  echo -e "  ${DIM}Family: ${family}  Params: ${param_size}  Quant: ${quant}  Context: ${ctx_len}${RESET}"

  # Runtime info from /api/ps (VRAM usage for loaded model)
  local ps_resp
  ps_resp=$(curl -sf "$OLLAMA_HOST/api/ps" 2>/dev/null) || return

  local model_size vram_size
  model_size=$(echo "$ps_resp" | jq -r --arg m "$model" '.models[] | select(.name == $m) | .size // 0')
  vram_size=$(echo "$ps_resp" | jq -r --arg m "$model" '.models[] | select(.name == $m) | .size_vram // 0')

  if [ -n "$model_size" ] && [ "$model_size" != "0" ] && [ "$model_size" != "null" ]; then
    local size_gb vram_gb
    size_gb=$(bytes_to_gb "$model_size")
    vram_gb=$(bytes_to_gb "$vram_size")
    local offload_pct
    if [ "$model_size" -gt 0 ]; then
      offload_pct=$(echo "scale=0; $vram_size * 100 / $model_size" | bc)
    else
      offload_pct="?"
    fi
    echo -e "  ${DIM}Model size: ${size_gb} GB  VRAM: ${vram_gb} GB (${offload_pct}% GPU offload)${RESET}"
  fi
}

# ── Benchmark one run ────────────────────────────────────────────────
# Uses /api/chat which reports eval_count including ALL generated tokens
# (thinking + visible), giving accurate tok/s regardless of thinking mode.
# Returns: gen_tps|prompt_tps|eval_count|total_duration_ns|load_duration_ns|eval_duration_ns|prompt_eval_duration_ns
bench_one() {
  local model="$1"

  # Build the user message with thinking control prefix
  local user_msg="$PROMPT"
  if [ "$THINKING" = "off" ]; then
    user_msg="/no_think
$PROMPT"
  elif [ "$THINKING" = "on" ]; then
    user_msg="/think
$PROMPT"
  fi

  local response
  response=$(curl -sf "$OLLAMA_HOST/api/chat" \
    -d "$(jq -n --arg m "$model" --arg msg "$user_msg" \
      '{model: $m, messages: [{role: "user", content: $msg}], stream: false, options: {num_predict: 512}}')" \
    2>/dev/null) || { echo "FAIL"; return; }

  local eval_count eval_duration_ns prompt_eval_count prompt_eval_duration_ns
  local total_duration_ns load_duration_ns
  eval_count=$(echo "$response" | jq -r '.eval_count // 0')
  eval_duration_ns=$(echo "$response" | jq -r '.eval_duration // 0')
  prompt_eval_count=$(echo "$response" | jq -r '.prompt_eval_count // 0')
  prompt_eval_duration_ns=$(echo "$response" | jq -r '.prompt_eval_duration // 0')
  total_duration_ns=$(echo "$response" | jq -r '.total_duration // 0')
  load_duration_ns=$(echo "$response" | jq -r '.load_duration // 0')

  if [ "$eval_duration_ns" -eq 0 ] || [ "$eval_count" -eq 0 ]; then
    echo "FAIL"
    return
  fi

  # eval_count from /api/chat includes ALL output tokens (thinking + visible)
  # so tok/s = total_tokens / total_generation_time → accurate throughput
  local gen_tps prompt_tps
  gen_tps=$(echo "scale=2; $eval_count / ($eval_duration_ns / 1000000000)" | bc)
  if [ "$prompt_eval_duration_ns" -gt 0 ]; then
    prompt_tps=$(echo "scale=2; $prompt_eval_count / ($prompt_eval_duration_ns / 1000000000)" | bc)
  else
    prompt_tps="N/A"
  fi

  echo "$gen_tps|$prompt_tps|$eval_count|$total_duration_ns|$load_duration_ns|$eval_duration_ns|$prompt_eval_duration_ns"
}

# ── Main ─────────────────────────────────────────────────────────────
check_deps

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║              Ollama Model Benchmark (tok/s)                     ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo ""

print_system_info

echo -e "${DIM}Prompt:    \"${PROMPT:0:60}...\"${RESET}"
echo -e "${DIM}Runs:      $NUM_RUNS per model${RESET}"
echo -e "${DIM}Thinking:  $THINKING${RESET}"
echo -e "${DIM}Max tokens: 512${RESET}"
echo ""

# Collect results for summary
declare -a result_models=()
declare -a result_gen_tps=()
declare -a result_prompt_tps=()
declare -a result_param_size=()
declare -a result_quant=()
declare -a result_vram=()
declare -a result_ollama_rss=()
declare -a result_sys_mem_delta=()

for model in "${MODELS[@]}"; do
  echo -e "${BOLD}── $model ──${RESET}"

  # Pull if not available locally
  if ! ollama list 2>/dev/null | grep -q "^$model"; then
    info "Pulling $model..."
    ollama pull "$model" || { echo -e "${RED}  ✗ Failed to pull $model, skipping${RESET}"; echo ""; continue; }
  fi

  # Unload any previously loaded model so memory baseline is clean
  info "Unloading previous models..."
  # Send keep_alive=0 to unload — we do this via a dummy request with immediate expiry
  local_loaded=$(curl -sf "$OLLAMA_HOST/api/ps" 2>/dev/null | jq -r '.models[].name // empty' 2>/dev/null || true)
  if [ -n "$local_loaded" ]; then
    while IFS= read -r loaded; do
      [ -z "$loaded" ] && continue
      curl -sf "$OLLAMA_HOST/api/generate" \
        -d "$(jq -n --arg m "$loaded" '{model: $m, keep_alive: 0}')" >/dev/null 2>&1 || true
    done <<< "$local_loaded"
  fi
  sleep 1  # brief pause for memory to settle

  # Snapshot memory BEFORE loading model
  mem_before=$(get_sys_mem)
  sys_used_before=$(echo "$mem_before" | cut -d'|' -f1)
  rss_before=$(get_ollama_rss)

  # Warm-up run (first run loads model into memory)
  info "Warming up (loading model)..."
  bench_one "$model" >/dev/null 2>&1

  # Snapshot memory AFTER loading model
  mem_after=$(get_sys_mem)
  sys_used_after=$(echo "$mem_after" | cut -d'|' -f1)
  sys_avail_after=$(echo "$mem_after" | cut -d'|' -f2)
  sys_total=$(echo "$mem_after" | cut -d'|' -f3)
  rss_after=$(get_ollama_rss)

  # Calculate deltas
  sys_mem_delta=$((sys_used_after - sys_used_before))
  rss_delta=$((rss_after - rss_before))

  # Show model info after warm-up (so it's loaded in /api/ps)
  print_model_info "$model"

  # Show memory stats
  echo -e "  ${DIM}Ollama RSS: $(bytes_to_gb "$rss_after") GB (+$(bytes_to_gb "$rss_delta") GB from loading)${RESET}"
  echo -e "  ${DIM}System RAM: $(bytes_to_gb "$sys_used_after") GB used / $(bytes_to_gb "$sys_total") GB total (+$(bytes_to_gb "$sys_mem_delta") GB from loading)${RESET}"

  # Grab static info for summary table
  local_show=$(curl -sf "$OLLAMA_HOST/api/show" -d "$(jq -n --arg m "$model" '{model: $m}')" 2>/dev/null)
  local_ps=$(curl -sf "$OLLAMA_HOST/api/ps" 2>/dev/null)
  m_param=$(echo "$local_show" | jq -r '.details.parameter_size // "?"')
  m_quant=$(echo "$local_show" | jq -r '.details.quantization_level // "?"')
  m_vram_bytes=$(echo "$local_ps" | jq -r --arg m "$model" '.models[] | select(.name == $m) | .size_vram // 0')
  if [ -n "$m_vram_bytes" ] && [ "$m_vram_bytes" != "0" ] && [ "$m_vram_bytes" != "null" ]; then
    m_vram="$(bytes_to_gb "$m_vram_bytes")G"
  else
    m_vram="?"
  fi

  gen_sum=0
  prompt_sum=0
  prompt_count=0
  total_dur_sum=0
  ok_runs=0

  for ((i = 1; i <= NUM_RUNS; i++)); do
    result=$(bench_one "$model")
    if [ "$result" = "FAIL" ]; then
      echo -e "  Run $i: ${RED}failed${RESET}"
      continue
    fi

    gen_tps=$(echo "$result" | cut -d'|' -f1)
    prompt_tps=$(echo "$result" | cut -d'|' -f2)
    tokens=$(echo "$result" | cut -d'|' -f3)
    total_dur=$(echo "$result" | cut -d'|' -f4)
    load_dur=$(echo "$result" | cut -d'|' -f5)
    eval_dur=$(echo "$result" | cut -d'|' -f6)
    prompt_dur=$(echo "$result" | cut -d'|' -f7)

    total_human=$(ns_to_human "$total_dur")
    eval_human=$(ns_to_human "$eval_dur")
    prompt_human=$(ns_to_human "$prompt_dur")

    echo -e "  Run $i: ${GREEN}${gen_tps} tok/s${RESET} (gen)  ${CYAN}${prompt_tps} tok/s${RESET} (prompt)  ${DIM}│ ${tokens} tok  total=${total_human}  gen=${eval_human}  prefill=${prompt_human}${RESET}"

    gen_sum=$(echo "$gen_sum + $gen_tps" | bc)
    total_dur_sum=$(echo "$total_dur_sum + $total_dur" | bc)
    if [ "$prompt_tps" != "N/A" ]; then
      prompt_sum=$(echo "$prompt_sum + $prompt_tps" | bc)
      prompt_count=$((prompt_count + 1))
    fi
    ok_runs=$((ok_runs + 1))
  done

  if [ "$ok_runs" -gt 0 ]; then
    avg_gen=$(echo "scale=2; $gen_sum / $ok_runs" | bc)
    avg_total_dur=$(ns_to_human "$(echo "$total_dur_sum / $ok_runs" | bc)")
    if [ "$prompt_count" -gt 0 ]; then
      avg_prompt=$(echo "scale=2; $prompt_sum / $prompt_count" | bc)
    else
      avg_prompt="N/A"
    fi
    result_models+=("$model")
    result_gen_tps+=("$avg_gen")
    result_prompt_tps+=("$avg_prompt")
    result_param_size+=("$m_param")
    result_quant+=("$m_quant")
    result_vram+=("$m_vram")
    result_ollama_rss+=("$(bytes_to_gb "$rss_after")G")
    result_sys_mem_delta+=("$(bytes_to_gb "$sys_mem_delta")G")
    echo -e "  ${BOLD}Avg: ${GREEN}${avg_gen} tok/s${RESET}${BOLD} (gen)  ${CYAN}${avg_prompt} tok/s${RESET}${BOLD} (prompt)  ${DIM}total=${avg_total_dur}${RESET}"
  else
    echo -e "  ${RED}All runs failed${RESET}"
  fi
  echo ""
done

# ── Summary table ────────────────────────────────────────────────────
if [ ${#result_models[@]} -eq 0 ]; then
  die "No successful benchmarks."
fi

printf "${BOLD}╔%-24s─%-8s─%-8s─%-8s─%-10s─%-10s─%-12s─%-12s╗${RESET}\n" \
  "════════════════════════" "════════" "════════" "════════" "══════════" "══════════" "════════════" "════════════"
printf "${BOLD}║%-24s │%-8s │%-8s │%-8s │%-10s │%-10s │%-12s │%-12s║${RESET}\n" \
  "         Summary" "" "" "" "" "" "" ""
printf "${BOLD}╠%-24s─%-8s─%-8s─%-8s─%-10s─%-10s─%-12s─%-12s╣${RESET}\n" \
  "════════════════════════" "════════" "════════" "════════" "══════════" "══════════" "════════════" "════════════"
printf "${BOLD}║ %-23s│ %-7s│ %-7s│ %-7s│ %-9s│ %-9s│ %-11s│ %-11s║${RESET}\n" \
  "Model" "Params" "Quant" "VRAM" "RSS" "RAM +/-" "Gen tok/s" "Prompt t/s"
printf "${BOLD}╠%-24s─%-8s─%-8s─%-8s─%-10s─%-10s─%-12s─%-12s╣${RESET}\n" \
  "════════════════════════" "════════" "════════" "════════" "══════════" "══════════" "════════════" "════════════"

best_idx=0
best_tps=0
for i in "${!result_models[@]}"; do
  gen="${result_gen_tps[$i]}"
  prompt="${result_prompt_tps[$i]}"
  params="${result_param_size[$i]}"
  quant="${result_quant[$i]}"
  vram="${result_vram[$i]}"
  rss="${result_ollama_rss[$i]}"
  mem_delta="${result_sys_mem_delta[$i]}"
  if (( $(echo "$gen > $best_tps" | bc -l) )); then
    best_tps="$gen"
    best_idx="$i"
  fi
  printf "║ %-23s│ %-7s│ %-7s│ %-7s│ %-9s│ %-9s│ %-11s│ %-11s║\n" \
    "${result_models[$i]}" "$params" "$quant" "$vram" "$rss" "+$mem_delta" "$gen" "$prompt"
done

printf "${BOLD}╚%-24s─%-8s─%-8s─%-8s─%-10s─%-10s─%-12s─%-12s╝${RESET}\n" \
  "════════════════════════" "════════" "════════" "════════" "══════════" "══════════" "════════════" "════════════"
echo ""
echo -e "${GREEN}★ Fastest generation: ${BOLD}${result_models[$best_idx]}${RESET}${GREEN} at ${result_gen_tps[$best_idx]} tok/s${RESET}"
echo ""
