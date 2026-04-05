#!/usr/bin/env bash
# provision-ec2.sh — Launch an EC2 t3.small running k3s for the tron cluster.
#
# Usage:
#   ./scripts/provision-ec2.sh --key-name MY_KEY_PAIR [--region us-east-1] [--profile default]
#
# After running:
#   1. SSH into the instance and run ./scripts/install-k3s.sh
#   2. Copy the printed KUBECONFIG_B64 into your HF Space secrets
#   3. Set INGRESS_HOST to the printed public IP in your HF Space secrets
#
# Requirements: aws CLI v2, jq

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

# Ubuntu 24.04 LTS (us-east-1). Update for other regions:
#   aws ec2 describe-images --owners 099720109477 \
#     --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
#     --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text
AMI_ID="ami-0c7217cdde317cfec"

usage() {
    echo "Usage: $0 --key-name KEY_PAIR [--region REGION] [--profile AWS_PROFILE]"
    exit 1
}

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key-name)  KEY_NAME="$2";  shift 2 ;;
        --region)    REGION="$2";    shift 2 ;;
        --profile)   PROFILE="$2";   shift 2 ;;
        *) usage ;;
    esac
done

[[ -z "$KEY_NAME" ]] && { echo "ERROR: --key-name is required"; usage; }

AWS="aws"
[[ -n "$PROFILE" ]] && AWS="aws --profile $PROFILE"

echo "[tron] Using region=$REGION instance=$INSTANCE_TYPE key=$KEY_NAME"

# ---------------------------------------------------------------------------
# Security group
# ---------------------------------------------------------------------------
echo "[tron] Creating security group '$SG_NAME'..."
SG_ID=$($AWS ec2 create-security-group \
    --region "$REGION" \
    --group-name "$SG_NAME" \
    --description "tron k3s cluster — SSH + k3s API + nginx" \
    --query 'GroupId' --output text 2>/dev/null) || \
SG_ID=$($AWS ec2 describe-security-groups \
    --region "$REGION" \
    --group-names "$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' --output text)

echo "[tron] Security group: $SG_ID"

authorize() {
    local proto="$1" port="$2" cidr="$3"
    $AWS ec2 authorize-security-group-ingress \
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
echo "[tron] Launching EC2 instance..."
INSTANCE_ID=$($AWS ec2 run-instances \
    --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME}]" \
    --query 'Instances[0].InstanceId' --output text)

echo "[tron] Instance launched: $INSTANCE_ID"
echo "[tron] Waiting for instance to be running..."

$AWS ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$($AWS ec2 describe-instances \
    --region "$REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

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
