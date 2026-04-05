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

# Port-forward the tron nginx service to localhost so the service probe
# (which hits 127.0.0.1:INGRESS_PORT) can reach it from inside this container.
# Runs as a self-restarting background loop.
start_ingress_port_forward() {
    local port="${INGRESS_PORT:-8080}"
    local namespace="${TRON_NAMESPACE:-tron}"
    local svc="${INGRESS_SVC:-nginx}"

    log "Starting port-forward: svc/${svc} -n ${namespace} -> 127.0.0.1:${port}"

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

    if [[ -n "${KUBECONFIG_B64:-}" ]]; then
        start_ingress_port_forward
    fi

    if [[ "$#" -eq 0 ]]; then
        set -- make ci
    fi

    exec "$@"
}

main "$@"
