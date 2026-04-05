#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
CONTRACT_TEST="tests.test_openenv_contract"

if ! command -v openenv >/dev/null 2>&1; then
  echo "openenv CLI not installed; falling back to contract test (${CONTRACT_TEST})."
  exec "${PYTHON_BIN}" -m unittest "${CONTRACT_TEST}" -q
fi

HELP_OUTPUT="$(openenv --help 2>&1 || true)"

if printf '%s\n' "${HELP_OUTPUT}" | grep -Eq '(^|[[:space:]])validate([[:space:]]|$)'; then
  echo "openenv CLI detected with validate support; running official validation."
  exec openenv validate openenv.yaml
fi

echo "openenv CLI is installed, but no documented 'validate' subcommand was found; falling back to contract test (${CONTRACT_TEST})."
exec "${PYTHON_BIN}" -m unittest "${CONTRACT_TEST}" -q
