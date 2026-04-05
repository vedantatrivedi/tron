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

# Keep a kubectl port-forward alive in the background so the service probe
# (which hits 127.0.0.1:INGRESS_PORT) can reach the cluster ingress.
# Controlled by INGRESS_PORT_FORWARD (default: enabled when KUBECONFIG_B64 is set).
# Override the target with INGRESS_NAMESPACE / INGRESS_SVC env vars.
start_ingress_port_forward() {
    local port="${INGRESS_PORT:-8080}"
    local namespace="${INGRESS_NAMESPACE:-ingress-nginx}"
    local svc="${INGRESS_SVC:-ingress-nginx-controller}"

    log "Starting ingress port-forward: svc/${svc} -n ${namespace} -> 127.0.0.1:${port}"

    (
        while true; do
            kubectl port-forward \
                -n "${namespace}" \
                "svc/${svc}" \
                "${port}:80" \
                --address 127.0.0.1 2>&1 | while IFS= read -r line; do
                    echo "[tron-container] port-forward: ${line}" >&2
                done
            echo "[tron-container] port-forward exited, restarting in 3s..." >&2
            sleep 3
        done
    ) &
}

main() {
    maybe_write_kubeconfig
    report_runtime_config

    # Start ingress port-forward when running against a remote cluster
    if [[ -n "${KUBECONFIG_B64:-}" ]] || [[ "${INGRESS_PORT_FORWARD:-}" == "true" ]]; then
        start_ingress_port_forward
    fi

    if [[ "$#" -eq 0 ]]; then
        set -- make ci
    fi

    exec "$@"
}

main "$@"
