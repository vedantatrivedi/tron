#!/usr/bin/env bash
# install-k3s.sh — Install k3s on the EC2 instance and print the kubeconfig.
#
# Run this on the EC2 instance after provision-ec2.sh:
#   ssh ubuntu@<PUBLIC_IP>
#   bash scripts/install-k3s.sh --public-ip <PUBLIC_IP>
#
# Output: prints KUBECONFIG_B64 to stdout — copy it into your HF Space secrets.

set -euo pipefail

log() { echo "[k3s-install] $*" >&2; }
die() { echo "[k3s-install] ERROR: $*" >&2; exit 1; }

PUBLIC_IP="${PUBLIC_IP:-}"
K3S_VERSION="${K3S_VERSION:-v1.31.5+k3s1}"

usage() {
    cat <<'EOF'
Usage: bash scripts/install-k3s.sh [--public-ip PUBLIC_IP] [--k3s-version VERSION]

Options:
  --public-ip PUBLIC_IP   Public IPv4 address to use in the kubeconfig and TLS SAN.
  --k3s-version VERSION   k3s version to install (default: v1.31.5+k3s1).

Environment variables:
  PUBLIC_IP               Alternative to --public-ip.
  K3S_VERSION             Alternative to --k3s-version.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --public-ip) PUBLIC_IP="$2"; shift 2 ;;
        --k3s-version) K3S_VERSION="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

detect_public_ip() {
    local token

    token="$(curl -sf --max-time 5 -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" || true)"
    if [[ -n "$token" ]]; then
        curl -sf --max-time 5 \
            -H "X-aws-ec2-metadata-token: $token" \
            http://169.254.169.254/latest/meta-data/public-ipv4
        return 0
    fi

    curl -sf --max-time 5 http://169.254.169.254/latest/meta-data/public-ipv4
}

# ---------------------------------------------------------------------------
# Detect public IP from EC2 metadata unless explicitly provided
# ---------------------------------------------------------------------------
if [[ -z "$PUBLIC_IP" ]]; then
    log "Detecting public IP..."
    PUBLIC_IP="$(detect_public_ip || true)"
fi

if [[ -z "$PUBLIC_IP" ]]; then
    die "Could not determine the public IP from EC2 metadata. Re-run with --public-ip <EC2_PUBLIC_IP>."
fi

log "Public IP: $PUBLIC_IP"

# ---------------------------------------------------------------------------
# Install k3s (single binary, matches kubectl v1.31.5 used by HF Space)
# ---------------------------------------------------------------------------
log "Installing k3s $K3S_VERSION..."
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="$K3S_VERSION" sh -s - \
    --tls-san "$PUBLIC_IP" \
    --disable=traefik \
    --disable=servicelb \
    --disable=metrics-server \
    --write-kubeconfig-mode=644

log "Waiting for k3s API server..."
timeout 60 bash -c 'until kubectl get nodes >/dev/null 2>&1; do sleep 2; done'
log "k3s is ready. Node status:"
kubectl get nodes

# ---------------------------------------------------------------------------
# Patch kubeconfig: replace 127.0.0.1 with public IP, then base64-encode
# ---------------------------------------------------------------------------
log "Generating kubeconfig for remote access..."
KUBECONFIG_B64=$(sed "s/127.0.0.1/$PUBLIC_IP/g" /etc/rancher/k3s/k3s.yaml | base64 -w 0)

echo ""
echo "=========================================="
echo " k3s installed successfully"
echo "=========================================="
echo " Set these secrets in your HF Space:"
echo ""
echo " INGRESS_HOST=$PUBLIC_IP"
echo " INGRESS_PORT=8080"
echo " KUBECONFIG_B64=$KUBECONFIG_B64"
echo "=========================================="
echo ""
echo " Verify remote access (from your laptop):"
echo "   echo \"\$KUBECONFIG_B64\" | base64 -d > /tmp/tron-kube.yaml"
echo "   KUBECONFIG=/tmp/tron-kube.yaml kubectl get nodes"
