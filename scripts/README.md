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
- `--ami-id ami-...` (if you want to override the auto-discovered Ubuntu 24.04 AMI)

Notes:
- `provision-ec2.sh` now auto-discovers the latest Ubuntu 24.04 AMI for the region you pass
- it also creates or reuses the `tron-k3s-sg` security group inside that region's default VPC
- if your region has no default VPC, the script will fail clearly and you should either create one or extend the script to accept an explicit VPC/subnet

---

## Step 3 — Install k3s (on the EC2 instance)

```bash
ssh -i ~/.ssh/tron-key.pem ubuntu@<PUBLIC_IP>
```

Once inside, run:

```bash
bash scripts/install-k3s.sh --public-ip <PUBLIC_IP>
```

Copy the printed values — you'll need them in Step 4.

Notes:
- `install-k3s.sh` accepts `--public-ip <PUBLIC_IP>` or `PUBLIC_IP=<PUBLIC_IP>` if you already know the public IP
- if you omit it, the script will try EC2 metadata first, including IMDSv2
- you can also override the version with `--k3s-version` or `K3S_VERSION=...`

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
