#!/usr/bin/env bash
set -euo pipefail

log() {
    echo "[tron-container] $*" >&2
}

die() {
    echo "[tron-container] ERROR: $*" >&2
    exit 1
}

maybe_write_kubeconfig() {
    local kubeconfig_path

    if [[ -z "${KUBECONFIG_B64:-}" ]]; then
        return 0
    fi

    kubeconfig_path="${KUBECONFIG_PATH:-/tmp/tron-kubeconfig.yaml}"
    if ! printf '%s' "${KUBECONFIG_B64}" | base64 -d > "${kubeconfig_path}"; then
        die "Failed to decode KUBECONFIG_B64 into ${kubeconfig_path}"
    fi
    chmod 600 "${kubeconfig_path}"
    export KUBECONFIG="${kubeconfig_path}"
    log "Loaded kubeconfig from KUBECONFIG_B64 into ${KUBECONFIG}"
}

report_runtime_config() {
    if [[ -n "${INGRESS_HOST:-}" ]]; then
        log "Using remote ingress host: ${INGRESS_HOST}"
    fi
    if [[ -n "${INGRESS_PORT:-}" ]]; then
        log "Using remote ingress port: ${INGRESS_PORT}"
    fi
}

main() {
    maybe_write_kubeconfig
    report_runtime_config

    if [[ "$#" -eq 0 ]]; then
        set -- make ci
    fi

    exec "$@"
}

main "$@"
