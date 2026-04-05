#!/usr/bin/env bash
# install-k3s.sh — Install k3s on the EC2 instance and print the kubeconfig.
#
# Run this on the EC2 instance after provision-ec2.sh:
#   ssh ubuntu@<PUBLIC_IP>
#   curl -fsSL <url> | bash
#   -- or --
#   bash install-k3s.sh
#
# Output: prints KUBECONFIG_B64 to stdout — copy it into your HF Space secrets.

set -euo pipefail

log() { echo "[k3s-install] $*" >&2; }

# ---------------------------------------------------------------------------
# Detect public IP from EC2 metadata
# ---------------------------------------------------------------------------
log "Detecting public IP..."
PUBLIC_IP=$(curl -sf --max-time 5 http://169.254.169.254/latest/meta-data/public-ipv4)
log "Public IP: $PUBLIC_IP"

# ---------------------------------------------------------------------------
# Install k3s (single binary, matches kubectl v1.31.5 used by HF Space)
# ---------------------------------------------------------------------------
log "Installing k3s v1.31.5+k3s1..."
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.31.5+k3s1" sh -s - \
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
