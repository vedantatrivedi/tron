#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
HOST_HEADER="${HOST_HEADER:-tron.localhost}"

curl_with_host() {
  curl -fsS -H "Host: ${HOST_HEADER}" "${BASE_URL}$1"
}

curl_with_host "/health"
printf '\n'
curl_with_host "/write?value=smoke-test"
printf '\n'
curl_with_host "/data"
printf '\n'
