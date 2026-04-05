---
title: tron
emoji: 🔷
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
tags:
  - openenv
---

```
 ████████╗██████╗  ██████╗ ███╗   ██╗
 ╚══██╔══╝██╔══██╗██╔═══██╗████╗  ██║
    ██║   ██████╔╝██║   ██║██╔██╗ ██║
    ██║   ██╔══██╗██║   ██║██║╚██╗██║
    ██║   ██║  ██║╚██████╔╝██║ ╚████║
    ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝
```

> *The cluster is already broken. Observability is partial. Root cause is hidden.*
> *You have 12 moves. Fix it — or get derezzed.*

---

## what is this

**tron** drops an agent into a live Kubernetes cluster mid-incident. No setup. No hints. Just a broken service and a step budget.

It measures **diagnosis-and-repair under pressure** — not code generation, not offline Q&A. The oracle doesn't care how you explain the fault. It cares whether `/data` returns `200`.

---

## the grid

A two-tier app: `nginx` → Redis-backed sidecar. One of 12 faults has been injected. The agent sees:

- black-box probes of `/health` and `/data`
- pod / service / deployment summaries
- one recent-change hint

It does **not** get logs, describe output, or event history unless it spends a turn asking.

---

## 12 scenarios

| scenario | type | difficulty |
|---|---|---|
| `bad-rollout-wrong-redis-host` | config | easy |
| `configmap-fixed-but-pods-stale` | config | medium |
| `service-selector-mismatch` | networking | easy |
| `networkpolicy-blocks-nginx-to-redis` | networking | medium |
| `ingress-path-rewrite-bug` | networking | medium |
| `networkpolicy-plus-secondary-drift` | compound | hard |
| `wrong-redis-host-plus-cpu-throttle` | compound | hard |
| `cpu-limits-too-low` | resource | medium |
| `memory-limits-too-low` | resource | medium |
| `readiness-probe-too-permissive` | probe | medium |
| `bridge-crashloop-bad-command` | crashloop | easy |
| `deployment-scaled-to-zero` | deployment | easy |

---

## scoring

| score | meaning |
|---|---|
| `1.0` | fully healthy |
| `0.7` | `/health` ok, data path degraded |
| `0.4` | reachable, major errors |
| `0.1` | timeout |
| `0.0` | unreachable |

Per-step reward: `Δ service_score + action_cost`

```
kubectl get / describe / logs / curl   →  0.00
kubectl exec                           → -0.02
kubectl apply / set                    → -0.05
kubectl rollout restart                → -0.10
kubectl scale                          → -0.15
kubectl delete                         → -0.30
```

Cheap reads are free. Destructive moves burn budget.

---

## API

The Space exposes a REST API on port `7860`:

| method | path | description |
|---|---|---|
| `GET` | `/info` | environment metadata, scenario list, action/observation schema |
| `POST` | `/reset` | start a new episode, returns first observation |
| `POST` | `/step` | execute one action, returns next observation + reward |
| `GET` | `/observation` | current observation without advancing state |
| `POST` | `/evaluate` | oracle verdict + repair score for the current episode |
| `GET` | `/health` | liveness probe |
| `GET` | `/docs` | interactive Swagger UI |

**Reset a scenario:**
```bash
curl -X POST https://<space>/reset \
  -H "Content-Type: application/json" \
  -d '{"scenario_id": "bad-rollout-wrong-redis-host", "seed": 11}'
```

**Take a step:**
```bash
curl -X POST https://<space>/step \
  -H "Content-Type: application/json" \
  -d '{"action": "kubectl -n tron get pods"}'
```

---

## run locally

```bash
# prerequisites: Docker, k3d, Python 3.9+
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
./setup.sh

# run a scenario with the naive baseline
python eval/run_eval.py --agent naive --scenario bad-rollout-wrong-redis-host --seed 11
python eval/summarize_results.py eval/results.jsonl

# run the LLM agent (copy .env.example → .env, add your key)
cp .env.example .env
python eval/run_eval.py --agent llm --output eval/llm-results.jsonl

# run everything
python eval/run_eval.py --agent all --output eval/results.jsonl
python eval/summarize_results.py eval/results.jsonl
```

```bash
make ci           # full local gate
make docker-smoke # containerized smoke
```

---

## remote cluster

Set these secrets and the container wires itself up automatically:

| var | value |
|---|---|
| `KUBECONFIG_B64` | base64-encoded kubeconfig for the remote k3s cluster |
| `INGRESS_HOST` | EC2 public IP |
| `INGRESS_PORT` | `8080` |

See [`scripts/README.md`](scripts/README.md) to provision the EC2 k3s cluster.
