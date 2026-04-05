#!/usr/bin/env bash
# provision-ec2.sh — Launch an EC2 t3.small running k3s for the tron cluster.
#
# Usage:
#   ./scripts/provision-ec2.sh --key-name MY_KEY_PAIR [--region us-east-1] [--profile default] [--ami-id ami-...]
#
# After running:
#   1. SSH into the instance and run ./scripts/install-k3s.sh
#   2. Copy the printed KUBECONFIG_B64 into your HF Space secrets
#   3. Set INGRESS_HOST to the printed public IP in your HF Space secrets
#
# Requirements: aws CLI v2

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
REGION="us-east-1"
INSTANCE_TYPE="t3.small"
SG_NAME="tron-k3s-sg"
TAG_NAME="tron-k3s"
KEY_NAME=""
PROFILE=""
AMI_ID=""

usage() {
    echo "Usage: $0 --key-name KEY_PAIR [--region REGION] [--profile AWS_PROFILE] [--ami-id AMI_ID]"
    exit 1
}

log() {
    echo "[tron] $*"
}

die() {
    echo "[tron] ERROR: $*" >&2
    exit 1
}

SG_DESCRIPTION="tron k3s cluster - SSH + k3s API + nginx"

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key-name)  KEY_NAME="$2";  shift 2 ;;
        --region)    REGION="$2";    shift 2 ;;
        --profile)   PROFILE="$2";   shift 2 ;;
        --ami-id)    AMI_ID="$2";    shift 2 ;;
        *) usage ;;
    esac
done

[[ -z "$KEY_NAME" ]] && { echo "ERROR: --key-name is required"; usage; }

AWS=(aws)
if [[ -n "$PROFILE" ]]; then
    AWS+=(--profile "$PROFILE")
fi

log "Using region=$REGION instance=$INSTANCE_TYPE key=$KEY_NAME"

# ---------------------------------------------------------------------------
# Discover network + image defaults for the target region
# ---------------------------------------------------------------------------
VPC_ID=$("${AWS[@]}" ec2 describe-vpcs \
    --region "$REGION" \
    --filters Name=isDefault,Values=true \
    --query 'Vpcs[0].VpcId' \
    --output text 2>/dev/null || true)

if [[ -z "$VPC_ID" || "$VPC_ID" == "None" ]]; then
    die "No default VPC found in region '$REGION'. Create one or update the script to accept a VPC/subnet override."
fi

if [[ -z "$AMI_ID" ]]; then
    AMI_ID=$("${AWS[@]}" ec2 describe-images \
        --region "$REGION" \
        --owners 099720109477 \
        --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
        --query 'sort_by(Images,&CreationDate)[-1].ImageId' \
        --output text 2>/dev/null || true)
fi

if [[ -z "$AMI_ID" || "$AMI_ID" == "None" ]]; then
    die "Could not resolve an Ubuntu 24.04 AMI for region '$REGION'. Re-run with --ami-id."
fi

log "Using default VPC: $VPC_ID"
log "Using AMI: $AMI_ID"

# ---------------------------------------------------------------------------
# Security group
# ---------------------------------------------------------------------------
log "Ensuring security group '$SG_NAME' exists in VPC '$VPC_ID'..."
SG_ID=$("${AWS[@]}" ec2 describe-security-groups \
    --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || true)

if [[ -z "$SG_ID" || "$SG_ID" == "None" ]]; then
    create_error_log="$(mktemp)"
    if ! SG_ID=$("${AWS[@]}" ec2 create-security-group \
        --region "$REGION" \
        --group-name "$SG_NAME" \
        --description "$SG_DESCRIPTION" \
        --vpc-id "$VPC_ID" \
        --query 'GroupId' \
        --output text 2>"$create_error_log"); then
        CREATE_ERROR=$(tr '\n' ' ' < "$create_error_log")
        rm -f "$create_error_log"
        die "Failed to create security group '$SG_NAME' in VPC '$VPC_ID': ${CREATE_ERROR}"
    fi
    rm -f "$create_error_log"
fi

if [[ -z "$SG_ID" || "$SG_ID" == "None" ]]; then
    die "Failed to create or locate security group '$SG_NAME' in VPC '$VPC_ID'."
fi

log "Security group: $SG_ID"

authorize() {
    local proto="$1" port="$2" cidr="$3"
    "${AWS[@]}" ec2 authorize-security-group-ingress \
        --region "$REGION" \
        --group-id "$SG_ID" \
        --protocol "$proto" --port "$port" --cidr "$cidr" 2>/dev/null || true
}

authorize tcp 22   0.0.0.0/0   # SSH
authorize tcp 6443 0.0.0.0/0   # k3s API server
authorize tcp 8080 0.0.0.0/0   # nginx (tron ingress)

# ---------------------------------------------------------------------------
# Launch instance
# ---------------------------------------------------------------------------
log "Launching EC2 instance..."
INSTANCE_ID=$("${AWS[@]}" ec2 run-instances \
    --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME}]" \
    --query 'Instances[0].InstanceId' --output text)

log "Instance launched: $INSTANCE_ID"
log "Waiting for instance to be running..."

"${AWS[@]}" ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$("${AWS[@]}" ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

if [[ -z "$PUBLIC_IP" || "$PUBLIC_IP" == "None" ]]; then
    die "Instance launched but no public IP was assigned. Check your subnet/VPC settings."
fi

echo ""
echo "=========================================="
echo " EC2 instance ready"
echo "=========================================="
echo " Instance ID : $INSTANCE_ID"
echo " Public IP   : $PUBLIC_IP"
echo " Region      : $REGION"
echo ""
echo " Next steps:"
echo "   1. Wait ~30s for SSH to become available, then:"
echo "      ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
echo ""
echo "   2. SSH in and install k3s (see scripts/README.md Step 3 for the commands)"
echo ""
echo "   3. Get the kubeconfig and set HF Space secrets:"
echo "      KUBECONFIG_B64: output of install-k3s.sh"
echo "      INGRESS_HOST: $PUBLIC_IP"
echo "      INGRESS_PORT: 8080"
echo "=========================================="
