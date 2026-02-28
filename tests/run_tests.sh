#!/usr/bin/env bash
# Lerim test runner — auto-activates venv and runs test groups.
set -euo pipefail

usage() {
  cat <<'USAGE'
Lerim test runner

Usage:
  tests/run_tests.sh [lint|unit|smoke|integration|e2e|quality|all] [options]

Groups:
  lint          Run ruff linter
  unit          Unit tests (no LLM calls)
  smoke         Smoke tests (quick LLM round-trips)
  integration   Integration tests (real LLM pipelines)
  e2e           End-to-end tests (full sync/maintain flows)
  quality       Compile check + pip check
  all           Run all groups in order

Options:
  --llm-provider PROVIDER
  --llm-model MODEL
  --llm-base-url URL
  --llm-fallback-provider PROVIDER
  --llm-fallback-model MODEL
  --llm-fallback-base-url URL
  --agent-provider PROVIDER
  --agent-model MODEL
  --agent-fallback-provider PROVIDER
  --agent-fallback-model MODEL
  --embeddings-provider PROVIDER
  --embeddings-model MODEL

Environment overrides are also supported (e.g. LERIM_LLM_PROVIDER).
USAGE
}

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Auto-activate venv if not already active ---
VENV_DIR="$ROOT_DIR/.venv"
if [[ -z "${VIRTUAL_ENV:-}" && -f "$VENV_DIR/bin/activate" ]]; then
  echo "Activating venv at $VENV_DIR"
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
elif [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "Warning: no .venv found at $VENV_DIR and no venv active."
  echo "Run: uv venv && source .venv/bin/activate && uv pip install -e ."
fi

# --- Load .env if present ---
ENV_FILE="$ROOT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

GROUP=${1:-unit}
if [[ "$GROUP" == "-h" || "$GROUP" == "--help" ]]; then
  usage
  exit 0
fi
shift || true

LLM_PROVIDER=${LLM_PROVIDER:-openrouter}
LLM_MODEL=${LLM_MODEL:-qwen/qwen3-coder-30b-a3b-instruct}
LLM_BASE_URL=${LLM_BASE_URL:-}
LLM_FALLBACK_PROVIDER=${LLM_FALLBACK_PROVIDER:-openrouter}
LLM_FALLBACK_MODEL=${LLM_FALLBACK_MODEL:-qwen/qwen3-coder-30b-a3b-instruct}
LLM_FALLBACK_BASE_URL=${LLM_FALLBACK_BASE_URL:-}

AGENT_PROVIDER=${AGENT_PROVIDER:-openrouter}
AGENT_MODEL=${AGENT_MODEL:-qwen/qwen3-coder-30b-a3b-instruct}
AGENT_FALLBACK_PROVIDER=${AGENT_FALLBACK_PROVIDER:-openrouter}
AGENT_FALLBACK_MODEL=${AGENT_FALLBACK_MODEL:-}

EMBEDDINGS_PROVIDER=${EMBEDDINGS_PROVIDER:-local}
EMBEDDINGS_MODEL=${EMBEDDINGS_MODEL:-Alibaba-NLP/gte-modernbert-base}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm-provider) LLM_PROVIDER="$2"; shift 2 ;;
    --llm-provider=*) LLM_PROVIDER="${1#*=}"; shift ;;
    --llm-model) LLM_MODEL="$2"; shift 2 ;;
    --llm-model=*) LLM_MODEL="${1#*=}"; shift ;;
    --llm-base-url) LLM_BASE_URL="$2"; shift 2 ;;
    --llm-base-url=*) LLM_BASE_URL="${1#*=}"; shift ;;
    --llm-fallback-provider) LLM_FALLBACK_PROVIDER="$2"; shift 2 ;;
    --llm-fallback-provider=*) LLM_FALLBACK_PROVIDER="${1#*=}"; shift ;;
    --llm-fallback-model) LLM_FALLBACK_MODEL="$2"; shift 2 ;;
    --llm-fallback-model=*) LLM_FALLBACK_MODEL="${1#*=}"; shift ;;
    --llm-fallback-base-url) LLM_FALLBACK_BASE_URL="$2"; shift 2 ;;
    --llm-fallback-base-url=*) LLM_FALLBACK_BASE_URL="${1#*=}"; shift ;;
    --agent-provider) AGENT_PROVIDER="$2"; shift 2 ;;
    --agent-provider=*) AGENT_PROVIDER="${1#*=}"; shift ;;
    --agent-model) AGENT_MODEL="$2"; shift 2 ;;
    --agent-model=*) AGENT_MODEL="${1#*=}"; shift ;;
    --agent-fallback-provider) AGENT_FALLBACK_PROVIDER="$2"; shift 2 ;;
    --agent-fallback-provider=*) AGENT_FALLBACK_PROVIDER="${1#*=}"; shift ;;
    --agent-fallback-model) AGENT_FALLBACK_MODEL="$2"; shift 2 ;;
    --agent-fallback-model=*) AGENT_FALLBACK_MODEL="${1#*=}"; shift ;;
    --embeddings-provider) EMBEDDINGS_PROVIDER="$2"; shift 2 ;;
    --embeddings-provider=*) EMBEDDINGS_PROVIDER="${1#*=}"; shift ;;
    --embeddings-model) EMBEDDINGS_MODEL="$2"; shift 2 ;;
    --embeddings-model=*) EMBEDDINGS_MODEL="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

print_section() {
  printf "\n== %s ==\n" "$1"
}

print_kv() {
  printf "  - %-24s %s\n" "$1" "$2"
}

print_section "Lerim test runner"
print_kv "Group" "$GROUP"
print_kv "Python" "$(command -v python || echo 'not found')"
print_kv "Venv" "${VIRTUAL_ENV:-not active}"
print_kv "LLM" "provider=$LLM_PROVIDER model=$LLM_MODEL"
print_kv "LLM fallback" "provider=$LLM_FALLBACK_PROVIDER model=$LLM_FALLBACK_MODEL"
print_kv "Agent" "provider=$AGENT_PROVIDER model=$AGENT_MODEL"
print_kv "Agent fallback" "provider=$AGENT_FALLBACK_PROVIDER model=${AGENT_FALLBACK_MODEL:-default}"
print_kv "Embeddings" "provider=$EMBEDDINGS_PROVIDER model=$EMBEDDINGS_MODEL"

key_status() {
  local key="$1"
  if [[ -n "${!key:-}" ]]; then
    echo "set"
  else
    echo "missing"
  fi
}
print_section "Key status"
print_kv "ZAI_API_KEY" "$(key_status ZAI_API_KEY)"
print_kv "ZAI_CODING_API_KEY" "$(key_status ZAI_CODING_API_KEY)"
print_kv "OPENAI_API_KEY" "$(key_status OPENAI_API_KEY)"
print_kv "OPENROUTER_API_KEY" "$(key_status OPENROUTER_API_KEY)"
print_kv "ANTHROPIC_API_KEY" "$(key_status ANTHROPIC_API_KEY)"

# Config comes from TOML layers now (src/lerim/config/default.toml -> ~/.lerim/config.toml -> project).
# Only API keys are read from env (ANTHROPIC_API_KEY, OPENROUTER_API_KEY, ZAI_API_KEY).
# Tests use LERIM_CONFIG env var to point at tests/test_config.toml (auto-applied by conftest.py).

# --- Ensure pytest is available ---
if ! command -v pytest >/dev/null 2>&1; then
  echo "ERROR: pytest not found. Activate venv or install: uv pip install -e '.[dev]'"
  exit 1
fi

# --- Run from project root so pytest can find tests module ---
cd "$ROOT_DIR"

run_unit() {
  print_section "Unit tests"
  python -m pytest tests/unit/ -x -q
  if command -v node >/dev/null 2>&1; then
    node tests/js_render_harness.js
  else
    echo "Node not found; skipping tests/js_render_harness.js"
  fi
}

run_pytest_allow_empty() {
  set +e
  python -m pytest "$@"
  status=$?
  set -e
  if [[ $status -eq 5 ]]; then
    echo "No tests collected for selector ($*); treating as pass."
    return 0
  fi
  return $status
}

run_integration() {
  print_section "Integration tests"
  export LERIM_INTEGRATION=1
  export LERIM_LLM_INTEGRATION=1
  export LERIM_EMBEDDINGS_INTEGRATION=1
  run_pytest_allow_empty tests/integration/
}

run_e2e() {
  print_section "End-to-end tests"
  export LERIM_E2E=1
  run_pytest_allow_empty tests/e2e/
}

run_smoke() {
  print_section "Smoke tests"
  export LERIM_SMOKE=1
  run_pytest_allow_empty tests/smoke/
}

run_lint() {
  print_section "Lint"
  if ! command -v ruff >/dev/null 2>&1; then
    echo "Ruff not found; install with: uv pip install -e \".[lint]\""
    return 1
  fi
  ruff check .
}

run_quality() {
  print_section "Quality checks"
  python -m compileall -q src/lerim
  if python -m pip --version >/dev/null 2>&1; then
    python -m pip check
    return
  fi
  if command -v uv >/dev/null 2>&1; then
    uv pip check
    return
  fi
  echo "pip check unavailable; skipping"
}

case "$GROUP" in
  unit)
    run_unit
    ;;
  integration)
    run_integration
    ;;
  e2e)
    run_e2e
    ;;
  smoke)
    run_smoke
    ;;
  lint)
    run_lint
    ;;
  quality)
    run_quality
    ;;
  all)
    run_lint
    run_unit
    run_smoke
    run_integration
    run_e2e
    run_quality
    ;;
  *)
    echo "Unknown group: $GROUP"
    usage
    exit 1
    ;;
esac
