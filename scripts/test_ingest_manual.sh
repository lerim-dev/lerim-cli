#!/bin/bash
# Manual testing script for OTLP Ingest API
# Usage: ./scripts/test_ingest_manual.sh [server_url]
#
# Prerequisites: Start the server first with:
#   python -m lerim.core.api.server

set -e

SERVER="${1:-http://localhost:8765}"
echo "Testing against: $SERVER"
echo "========================================"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() { echo -e "${GREEN}✓ PASS${NC}: $1"; }
fail() { echo -e "${RED}✗ FAIL${NC}: $1"; exit 1; }
info() { echo -e "${YELLOW}→${NC} $1"; }

# Test 1: JSON Batch Ingest
echo ""
info "Test 1: JSON Batch Ingest (POST /api/v1/ingest)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/ingest" \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "test-ingest-001",
    "agent_type": "claude",
    "status": "completed",
    "started_at": "2026-01-26T10:00:00Z",
    "ended_at": "2026-01-26T10:05:00Z",
    "messages": [
      {"role": "user", "content": "Write a hello world function", "timestamp": "2026-01-26T10:00:01Z"},
      {"role": "assistant", "content": "Here is a hello world function:\n\ndef hello():\n    print(\"Hello, World!\")", "timestamp": "2026-01-26T10:00:02Z", "model": "claude-3-opus"}
    ],
    "tool_calls": [
      {"name": "write_file", "status": "completed", "input": {"path": "hello.py"}, "duration_ms": 150}
    ],
    "file_changes": [
      {"path": "hello.py", "change_type": "created", "summary": "Created hello world function"}
    ]
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "201" ] || [ "$HTTP_CODE" = "200" ]; then
  pass "JSON batch ingest returned $HTTP_CODE"
  echo "   Response: $BODY"
else
  fail "Expected 200/201, got $HTTP_CODE: $BODY"
fi

# Test 2: Create Run (Streaming)
echo ""
info "Test 2: Create Run (POST /api/v1/runs)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/runs" \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "test-stream-001",
    "agent_type": "codex",
    "status": "running",
    "started_at": "2026-01-26T11:00:00Z"
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "201" ]; then
  pass "Create run returned 201"
  echo "   Response: $BODY"
else
  fail "Expected 201, got $HTTP_CODE: $BODY"
fi

# Test 3: Append Message
echo ""
info "Test 3: Append Message (POST /api/v1/runs/{id}/messages)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/runs/test-stream-001/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "user",
    "content": "Please fix the bug in auth.py",
    "timestamp": "2026-01-26T11:00:01Z"
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Append message returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 4: Append Tool Call
echo ""
info "Test 4: Append Tool Call (POST /api/v1/runs/{id}/tool-calls)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/runs/test-stream-001/tool-calls" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "read_file",
    "status": "completed",
    "input": {"path": "auth.py"},
    "output": {"content": "def authenticate(): pass"},
    "duration_ms": 50
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Append tool call returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 5: Append File Change
echo ""
info "Test 5: Append File Change (POST /api/v1/runs/{id}/file-changes)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/runs/test-stream-001/file-changes" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "auth.py",
    "change_type": "modified",
    "summary": "Fixed authentication bug",
    "bytes_changed": 256
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Append file change returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 6: Append Terminal Command
echo ""
info "Test 6: Append Terminal Command (POST /api/v1/runs/{id}/terminal-commands)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/runs/test-stream-001/terminal-commands" \
  -H "Content-Type: application/json" \
  -d '{
    "command": "pytest tests/test_auth.py",
    "cwd": "/app",
    "exit_code": 0,
    "stdout_snippet": "1 passed in 0.5s"
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Append terminal command returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 7: Append Error
echo ""
info "Test 7: Append Error (POST /api/v1/runs/{id}/errors)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/runs/test-stream-001/errors" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "API rate limit exceeded",
    "error_type": "RateLimitError",
    "severity": "warning"
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Append error returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 8: Update Run Status
echo ""
info "Test 8: Update Run (PATCH /api/v1/runs/{id})"
RESPONSE=$(curl -s -w "\n%{http_code}" -X PATCH "$SERVER/api/v1/runs/test-stream-001" \
  -H "Content-Type: application/json" \
  -d '{
    "status": "completed",
    "ended_at": "2026-01-26T11:05:00Z"
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Update run returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 9: Get Run
echo ""
info "Test 9: Get Run (GET /api/v1/runs/{id})"
RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$SERVER/api/v1/runs/test-stream-001")

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "Get run returned 200"
  echo "   Response (truncated): $(echo $BODY | head -c 200)..."
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 10: OTLP JSON Ingest
echo ""
info "Test 10: OTLP JSON Ingest (POST /v1/traces)"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/v1/traces" \
  -H "Content-Type: application/json" \
  -d '{
    "resourceSpans": [{
      "resource": {
        "attributes": [
          {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
          {"key": "service.namespace", "value": {"stringValue": "test-workspace"}}
        ]
      },
      "scopeSpans": [{
        "spans": [
          {
            "traceId": "0102030405060708090a0b0c0d0e0f10",
            "spanId": "0102030405060708",
            "parentSpanId": "",
            "name": "otlp-test-run",
            "startTimeUnixNano": "1706270400000000000",
            "endTimeUnixNano": "1706270700000000000",
            "status": {"code": 1},
            "attributes": [],
            "events": []
          },
          {
            "traceId": "0102030405060708090a0b0c0d0e0f10",
            "spanId": "0203040506070809",
            "parentSpanId": "0102030405060708",
            "name": "message",
            "startTimeUnixNano": "1706270401000000000",
            "endTimeUnixNano": "1706270402000000000",
            "status": {},
            "attributes": [
              {"key": "gen_ai.message.role", "value": {"stringValue": "user"}},
              {"key": "gen_ai.prompt", "value": {"stringValue": "Hello from OTLP!"}}
            ],
            "events": []
          },
          {
            "traceId": "0102030405060708090a0b0c0d0e0f10",
            "spanId": "0304050607080910",
            "parentSpanId": "0102030405060708",
            "name": "message",
            "startTimeUnixNano": "1706270403000000000",
            "endTimeUnixNano": "1706270404000000000",
            "status": {},
            "attributes": [
              {"key": "gen_ai.message.role", "value": {"stringValue": "assistant"}},
              {"key": "gen_ai.completion", "value": {"stringValue": "Hello! How can I help you today?"}},
              {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4"}},
              {"key": "gen_ai.usage.input_tokens", "value": {"intValue": 5}},
              {"key": "gen_ai.usage.output_tokens", "value": {"intValue": 10}}
            ],
            "events": []
          }
        ]
      }]
    }]
  }')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "OTLP JSON ingest returned 200"
  echo "   Response: $BODY"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 11: Get OTLP-ingested run
echo ""
info "Test 11: Verify OTLP run was stored (GET /api/v1/runs/{id})"
RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$SERVER/api/v1/runs/0102030405060708090a0b0c0d0e0f10")

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "200" ]; then
  pass "OTLP run retrieved successfully"
  echo "   Agent type: $(echo $BODY | python3 -c 'import sys,json; print(json.load(sys.stdin).get("agent_type","?"))' 2>/dev/null || echo 'parse error')"
  echo "   Messages: $(echo $BODY | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("messages",[])))' 2>/dev/null || echo 'parse error')"
else
  fail "Expected 200, got $HTTP_CODE: $BODY"
fi

# Test 12: Error handling - invalid JSON
echo ""
info "Test 12: Error Handling - Invalid JSON"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/ingest" \
  -H "Content-Type: application/json" \
  -d 'not valid json')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "400" ]; then
  pass "Invalid JSON correctly returned 400"
  echo "   Response: $BODY"
else
  fail "Expected 400, got $HTTP_CODE"
fi

# Test 13: Error handling - missing fields
echo ""
info "Test 13: Error Handling - Missing Required Fields"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$SERVER/api/v1/ingest" \
  -H "Content-Type: application/json" \
  -d '{"status": "completed"}')

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "400" ]; then
  pass "Missing fields correctly returned 400"
  echo "   Response: $BODY"
else
  fail "Expected 400, got $HTTP_CODE"
fi

# Test 14: Error handling - run not found
echo ""
info "Test 14: Error Handling - Run Not Found"
RESPONSE=$(curl -s -w "\n%{http_code}" -X GET "$SERVER/api/v1/runs/nonexistent-run-12345")

HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "404" ]; then
  pass "Non-existent run correctly returned 404"
  echo "   Response: $BODY"
else
  fail "Expected 404, got $HTTP_CODE"
fi

# Summary
echo ""
echo "========================================"
echo -e "${GREEN}All API tests passed!${NC}"
echo ""
echo "Ingested test data:"
echo "  - test-ingest-001 (claude, batch ingest)"
echo "  - test-stream-001 (codex, streaming ingest)"
echo "  - 0102030405060708090a0b0c0d0e0f10 (openai, OTLP ingest)"
echo ""
echo "Next steps for UI testing:"
echo "  1. Open $SERVER in browser"
echo "  2. Navigate to Runs tab"
echo "  3. Verify these 3 runs appear in the list"
echo "  4. Click on each to verify details (messages, tool calls)"
echo "========================================"
