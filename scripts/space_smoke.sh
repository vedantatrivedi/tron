#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${TRON_SPACE_URL:-http://127.0.0.1:7860}}"
TASK_ID="${TASK_ID:-easy}"
SEED="${SEED:-11}"
STEP_COMMAND="${STEP_COMMAND:-kubectl -n tron get service redis -o yaml}"

BASE_URL="${BASE_URL%/}"

emit_json() {
  python3 -c 'import json, sys; print(json.dumps(json.load(sys.stdin), indent=2, sort_keys=True))'
}

json_body() {
  python3 -c "$1"
}

echo "[space-smoke] base_url=${BASE_URL}"
echo "[space-smoke] task_id=${TASK_ID} seed=${SEED}"
echo "[space-smoke] step_command=${STEP_COMMAND}"

echo
echo "== GET /health =="
curl -fsS "${BASE_URL}/health" | emit_json

echo
echo "== POST /reset =="
RESET_PAYLOAD="$(TASK_ID="${TASK_ID}" SEED="${SEED}" json_body 'import json, os; print(json.dumps({"task_id": os.environ["TASK_ID"], "seed": int(os.environ["SEED"])}))')"
curl -fsS \
  -X POST "${BASE_URL}/reset" \
  -H "Content-Type: application/json" \
  -d "${RESET_PAYLOAD}" | emit_json

echo
echo "== POST /step =="
STEP_PAYLOAD="$(STEP_COMMAND="${STEP_COMMAND}" json_body 'import json, os; print(json.dumps({"command": os.environ["STEP_COMMAND"]}))')"
curl -fsS \
  -X POST "${BASE_URL}/step" \
  -H "Content-Type: application/json" \
  -d "${STEP_PAYLOAD}" | emit_json

echo
echo "== GET /state =="
curl -fsS "${BASE_URL}/state" | emit_json

echo
echo "[space-smoke] passed"
