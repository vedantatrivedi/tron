#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-tron-lab}"

if command -v k3d >/dev/null 2>&1; then
  if k3d cluster list | awk '{print $1}' | grep -Fxq "${CLUSTER_NAME}"; then
    k3d cluster delete "${CLUSTER_NAME}"
  fi
fi
