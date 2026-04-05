# EC2 Setup Scripts

These scripts provision the EC2 instance that runs the tron k3s cluster.

## Prerequisites

- AWS CLI v2 installed and configured (`aws configure`)
- An AWS account with EC2 permissions

---

## Step 1 — Create a key pair (first time only)

```bash
aws ec2 create-key-pair \
  --key-name tron-key \
  --query 'KeyMaterial' \
  --output text > ~/.ssh/tron-key.pem

chmod 400 ~/.ssh/tron-key.pem
```

---

## Step 2 — Launch the EC2 instance (from your laptop)

```bash
./scripts/provision-ec2.sh --key-name tron-key
```

Wait ~30 seconds after it prints the public IP before SSHing in.

Optional flags:
- `--region us-east-1` (default)
- `--profile my-aws-profile` (if using named AWS profiles)

---

## Step 3 — Install k3s (on the EC2 instance)

```bash
ssh -i ~/.ssh/tron-key.pem ubuntu@<PUBLIC_IP>
```

Once inside, run:

```bash
PUBLIC_IP=$(curl -sf http://169.254.169.254/latest/meta-data/public-ipv4)

curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION="v1.31.5+k3s1" sh -s - \
    --tls-san "$PUBLIC_IP" \
    --disable=traefik --disable=servicelb --disable=metrics-server \
    --write-kubeconfig-mode=644

# Wait for it to be ready
timeout 60 bash -c 'until kubectl get nodes >/dev/null 2>&1; do sleep 2; done'

# Print the kubeconfig (base64-encoded) and public IP for HF Space secrets
echo "INGRESS_HOST=$PUBLIC_IP"
echo "KUBECONFIG_B64=$(sed "s/127.0.0.1/$PUBLIC_IP/g" /etc/rancher/k3s/k3s.yaml | base64 -w 0)"
```

Copy the printed values — you'll need them in Step 4.

---

## Step 4 — Set HF Space secrets

In your Hugging Face Space settings, add:

| Secret | Value |
|--------|-------|
| `KUBECONFIG_B64` | printed by install-k3s.sh |
| `INGRESS_HOST` | EC2 public IP |
| `INGRESS_PORT` | `8080` |

---

## Step 5 — Deploy

```bash
git push hf main
```

The Space will restart and connect to your EC2 cluster automatically.
