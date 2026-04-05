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

# Auto-detect the ingress controller's external IP so the service probe can
# reach it directly. Tries nginx ingress then traefik (k3s default).
# Sets INGRESS_URL_HOST and INGRESS_PORT=80 if not already configured.
auto_detect_ingress_host() {
    if [[ -n "${INGRESS_URL_HOST:-}" ]]; then
        log "Using configured ingress host: ${INGRESS_URL_HOST}:${INGRESS_PORT:-80}"
        return 0
    fi

    local ip=""

    # nginx ingress controller (ip, then hostname fallback)
    ip=$(kubectl get svc -n ingress-nginx ingress-nginx-controller \
        -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    if [[ -z "$ip" ]]; then
        ip=$(kubectl get svc -n ingress-nginx ingress-nginx-controller \
            -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    fi

    # traefik (k3s default, ip then hostname)
    if [[ -z "$ip" ]]; then
        ip=$(kubectl get svc -n kube-system traefik \
            -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
    fi
    if [[ -z "$ip" ]]; then
        ip=$(kubectl get svc -n kube-system traefik \
            -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    fi

    if [[ -n "$ip" ]]; then
        export INGRESS_URL_HOST="$ip"
        export INGRESS_PORT="${INGRESS_PORT:-80}"
        log "Auto-detected ingress at ${INGRESS_URL_HOST}:${INGRESS_PORT}"
    else
        log "WARNING: could not auto-detect ingress external IP; service probe may show unreachable"
        log "  Set INGRESS_URL_HOST and INGRESS_PORT manually to fix this"
    fi
}

main() {
    maybe_write_kubeconfig
    report_runtime_config

    if [[ -n "${KUBECONFIG_B64:-}" ]]; then
        auto_detect_ingress_host
    fi

    if [[ "$#" -eq 0 ]]; then
        set -- make ci
    fi

    exec "$@"
}

main "$@"
