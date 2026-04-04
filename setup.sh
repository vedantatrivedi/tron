#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME="${CLUSTER_NAME:-tron-lab}"
CLUSTER_CONTEXT="k3d-${CLUSTER_NAME}"
NAMESPACE="${NAMESPACE:-tron}"
INGRESS_HOST="${INGRESS_HOST:-tron.localhost}"
INGRESS_PORT="${INGRESS_PORT:-8080}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
NGINX_IMAGE="${NGINX_IMAGE:-nginx:1.27-alpine}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-alpine}"
IMAGES=(
  "${REDIS_IMAGE}"
  "${NGINX_IMAGE}"
  "${PYTHON_IMAGE}"
)

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "$1 is required" >&2
    exit 1
  fi
}

ensure_local_image() {
  local image="$1"
  if ! docker image inspect "${image}" >/dev/null 2>&1; then
    docker pull "${image}"
  fi
}

wait_for_http() {
  local path="$1"
  local expected="${2:-}"
  local attempt

  for attempt in $(seq 1 30); do
    local body
    if body="$(curl -fsS -H "Host: ${INGRESS_HOST}" "http://127.0.0.1:${INGRESS_PORT}${path}")"; then
      if [[ -z "${expected}" ]] || [[ "${body}" == *"${expected}"* ]]; then
        return 0
      fi
    fi
    sleep 2
  done

  echo "timed out waiting for ${path}" >&2
  return 1
}

require_bin k3d
require_bin kubectl
require_bin curl
require_bin docker

for image in "${IMAGES[@]}"; do
  ensure_local_image "${image}"
done

if ! k3d cluster list | awk '{print $1}' | grep -Fxq "${CLUSTER_NAME}"; then
  k3d cluster create "${CLUSTER_NAME}" \
    --agents 1 \
    --port "${INGRESS_PORT}:80@loadbalancer"
fi

kubectl config use-context "${CLUSTER_CONTEXT}" >/dev/null
k3d image import -c "${CLUSTER_NAME}" "${IMAGES[@]}"

kubectl apply -f "${ROOT_DIR}/manifests/namespace.yaml"
kubectl apply -f "${ROOT_DIR}/manifests/configmap.yaml"
kubectl apply -f "${ROOT_DIR}/manifests/redis.yaml"
kubectl apply -f "${ROOT_DIR}/manifests/nginx.yaml"
kubectl apply -f "${ROOT_DIR}/manifests/ingress.yaml"
kubectl apply -f "${ROOT_DIR}/manifests/networkpolicy-base.yaml"
kubectl -n "${NAMESPACE}" set image deployment/redis redis="${REDIS_IMAGE}" >/dev/null
kubectl -n "${NAMESPACE}" set image deployment/nginx nginx="${NGINX_IMAGE}" redis-bridge="${PYTHON_IMAGE}" >/dev/null

kubectl -n "${NAMESPACE}" rollout status deployment/redis --timeout=240s
kubectl -n "${NAMESPACE}" rollout status deployment/nginx --timeout=240s

# Baseline verification: health is frontend-only, while /data exercises redis.
wait_for_http "/health" "ok"
wait_for_http "/write?value=baseline" "\"status\": \"stored\""
wait_for_http "/data" "\"value\": \"baseline\""

echo "tron baseline is ready on http://127.0.0.1:${INGRESS_PORT}"
