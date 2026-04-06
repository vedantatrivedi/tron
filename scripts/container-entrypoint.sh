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

# Detect the k3s node's external IP and point the service probe at it.
# The nginx service is type LoadBalancer; k3s ServiceLB exposes it on the
# node's IP:80. We prefer ExternalIP (cloud VPS public IP) over InternalIP.
auto_detect_ingress() {
    if [[ -n "${INGRESS_URL_HOST:-}" ]]; then
        log "Using configured ingress: ${INGRESS_URL_HOST}:${INGRESS_PORT:-80}"
        return 0
    fi

    local node_ip=""

    node_ip=$(kubectl get nodes \
        -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' \
        2>/dev/null || true)

    if [[ -z "$node_ip" ]]; then
        node_ip=$(kubectl get nodes \
            -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' \
            2>/dev/null || true)
    fi

    if [[ -n "$node_ip" ]]; then
        export INGRESS_URL_HOST="$node_ip"
        export INGRESS_PORT="${INGRESS_PORT:-80}"
        log "Ingress target: ${INGRESS_URL_HOST}:${INGRESS_PORT}"
    else
        log "WARNING: could not detect node IP; set INGRESS_URL_HOST and INGRESS_PORT manually"
    fi
}

verify_cluster_connectivity() {
    log "Verifying cluster connectivity..."
    if ! kubectl cluster-info --request-timeout=5s >/dev/null 2>&1; then
        die "Kubernetes cluster is not reachable after loading KUBECONFIG_B64. Check that the credentials are valid and the cluster is running."
    fi
    log "Cluster connectivity verified."
}

main() {
    maybe_write_kubeconfig
    report_runtime_config

    if [[ -n "${KUBECONFIG_B64:-}" ]]; then
        auto_detect_ingress
        verify_cluster_connectivity
    else
        log "WARNING: KUBECONFIG_B64 is not set. POST /reset will return HTTP 503 until cluster credentials are provided."
    fi

    if [[ "$#" -eq 0 ]]; then
        set -- make ci
    fi

    exec "$@"
}

main "$@"
