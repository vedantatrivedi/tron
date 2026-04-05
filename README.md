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

> The cluster is already broken. Observability is partial. Root cause is hidden.  
> You get a step budget, a shell-shaped action space, and one job: repair the system before the oracle times out.

# tron

`tron` is a live Kubernetes incident benchmark and OpenEnv-style environment for evaluating diagnosis-and-repair agents under partial observability.

This is a benchmark, not a product. The point is to measure whether an agent can repair realistic cluster incidents under pressure, not whether it can narrate the fault elegantly.

The benchmark core runs deterministic mutations against a disposable `k3d` or remote k3s cluster. The OpenEnv wrapper exposes a typed HTTP API with:

- `POST /reset`
- `POST /step`
- `GET /state`
- typed Pydantic task, action, observation, reward, and state models
- a root [`inference.py`](inference.py) baseline that uses the OpenAI client

---

## What This Is

**tron** drops an agent into a live Kubernetes cluster mid-incident. No setup wizard. No full logs by default. Just a broken service, a recent-change hint, and a bounded action budget.

It evaluates **diagnosis and repair under pressure**:

- a live cluster is already broken
- observability is partial by default
- recent changes are hints, not diagnoses
- the agent must choose commands under an action-cost budget
- success depends on black-box recovery and durable repair, not just a workaround

That makes it useful for evaluating tool-using incident-response agents instead of static infrastructure trivia.

---

## Why It’s Interesting

Most benchmarks stop at code generation or offline reasoning. `tron` focuses on the operational loop:

1. classify a live failure from sparse symptoms
2. choose the next command under time and step pressure
3. repair the right object
4. re-probe
5. keep going until the underlying incident is actually fixed

The oracle does not reward “good explanations.” It rewards real recovery.

---

## Architecture

`tron` has five core layers:

- runtime app: a small two-tier app with `nginx` in front of a Redis-backed sidecar path. `/health` is intentionally shallow and `/data` exercises the backend path.
- scenario catalog: [`tron/scenario_catalog.py`](tron/scenario_catalog.py) defines single-root-cause and compound incident templates, plus seeded parameter variation.
- incident engine: [`tron/incident_engine.py`](tron/incident_engine.py) applies deterministic cluster mutations and verifies that the intended fault activated.
- environment loop: [`tron/env.py`](tron/env.py) handles `reset()`, `step(action)`, reward computation, and termination.
- oracle and eval: [`tron/oracle.py`](tron/oracle.py), [`eval/run_eval.py`](eval/run_eval.py), and [`eval/summarize_results.py`](eval/summarize_results.py) score black-box recovery and summarize agent behavior.

The OpenEnv-facing wrapper lives under [`tron_openenv/`](tron_openenv/):

- [`tron_openenv/models.py`](tron_openenv/models.py): typed task, action, observation, reward, and state models
- [`tron_openenv/client.py`](tron_openenv/client.py): HTTP client for `reset()`, `step()`, and `state()`
- [`tron_openenv/server/environment.py`](tron_openenv/server/environment.py): adapter from the benchmark core to the official task API
- [`tron_openenv/server/app.py`](tron_openenv/server/app.py): FastAPI server used by the Docker image

---

## Official OpenEnv Tasks

The submission surface currently exposes three deterministic tasks:

| task | scenario | difficulty | objective |
|---|---|---|---|
| `easy` | `service-selector-mismatch` | easy | Repair service-to-pod wiring so nginx can reach redis again. |
| `medium` | `bad-rollout-wrong-redis-host` | medium | Repair config drift and ensure the serving workload picks up the durable fix. |
| `hard` | `networkpolicy-plus-secondary-drift` | hard | Repair a compound outage spanning both policy and routing drift. |

These are the tasks the root [`inference.py`](inference.py) baseline runs by default.

---

## Baseline Snapshot

Latest measured OpenEnv baseline run:

- model: `gpt-5-mini`
- seed: `11`
- command: `.venv/bin/python inference.py --env-base-url http://127.0.0.1:8000 --seed 11`

Observed scores:

| task | scenario | oracle score | steps | verdict |
|---|---|---:|---:|---|
| `easy` | `service-selector-mismatch` | `0.85` | `12` | `failure` |
| `medium` | `bad-rollout-wrong-redis-host` | `0.50` | `15` | `failure` |
| `hard` | `networkpolicy-plus-secondary-drift` | `0.60` | `18` | `failure` |

This is an honest baseline, not a tuned best-case run. The OpenEnv wrapper is stable and reproducible, but the current model baseline still underperforms on durable repair closure.

---

## What The Agent Sees

Each episode starts with a broken service and a compact observation bundle:

- a black-box probe of `/health` and `/data`
- compact summaries of pods, services, deployments, and endpoints
- one recent-change hint
- the previous action and reward

The agent does **not** automatically get:

- full logs
- full `kubectl describe`
- full event history
- direct diagnosis text

If it wants more evidence, it must spend a turn on `kubectl` or `curl`.

---

## Action, Observation, And State Spaces

Action space:

- one typed action per turn: `{"command": "kubectl ..."}`
- commands must begin with `kubectl` or `curl`
- the runtime rejects interactive commands and benchmark-breaking shortcuts

Observation space:

- `incident_brief`
- `step_count`
- `last_action`
- `last_reward`
- `service_probe`
  - `health_status`
  - `data_status`
  - `http_status`
  - `latency_ms`
  - `score`
- `cluster_summary`
  - `pods`
  - `services`
  - `deployments`
  - `endpoints`
- `recent_change_hint`
- `done`

State space:

- `episode_id`
- current `task` and `scenario_id`
- `seed`
- `step_count`
- `cumulative_reward`
- `last_action`
- `last_reward`
- `service_score`
- `oracle_score`
- `oracle_verdict`

---

## Oracle And Reward Design

The oracle behaves like a black-box SLI evaluator. It checks reachability, HTTP status, latency, and the difference between `/health` and `/data`. It does not diagnose root cause.

Service score buckets:

| score | meaning |
|---|---|
| `1.0` | fully healthy |
| `0.7` | `/health` works but data path is degraded |
| `0.4` | reachable with major errors |
| `0.1` | timeout |
| `0.0` | unreachable |

Per-step reward is:

`new_service_score - previous_service_score + action_cost`

Action costs:

```text
kubectl get / describe / logs / top / rollout history / curl   ->  0.00
kubectl exec                                                   -> -0.02
kubectl apply / set                                            -> -0.05
kubectl edit                                                   -> -0.08
kubectl rollout restart                                        -> -0.10
kubectl scale                                                  -> -0.15
kubectl delete                                                 -> -0.30
```

Cheap reads are free. Destructive moves burn budget. The final oracle score combines black-box recovery with scenario-specific repair checks, so workaround recoveries can still fail.

---

## Scenario Catalog

The current benchmark catalog includes 12 scenarios:

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

Recommended demo scenarios:

- `bad-rollout-wrong-redis-host`
- `networkpolicy-blocks-nginx-to-redis`
- `wrong-redis-host-plus-cpu-throttle`

Those three are also the default scenarios in [`eval/seeds.yaml`](eval/seeds.yaml).

---

## Benchmark Status

Current stronger scenarios for the structured smart baseline:

- `bad-rollout-wrong-redis-host`
- `configmap-fixed-but-pods-stale`
- `service-selector-mismatch`
- `readiness-probe-too-permissive`
- `networkpolicy-blocks-nginx-to-redis`
- `ingress-path-rewrite-bug`
- `networkpolicy-plus-secondary-drift`

Current weaker scenarios:

- `cpu-limits-too-low`
- `memory-limits-too-low`
- `wrong-redis-host-plus-cpu-throttle`

Expected smart-agent behavior:

- stay in namespace `tron`
- use one targeted read to choose a failure domain
- apply a durable fix rather than a temporary override
- re-probe `/health` and `/data` after each repair
- continue after workaround recovery until the oracle repair checks pass

---

## HTTP API

The OpenEnv-style server exposes:

| method | path | description |
|---|---|---|
| `GET` | `/` | metadata and task list |
| `GET` | `/health` | liveness |
| `GET` | `/tasks` | official task catalog |
| `POST` | `/reset` | start an episode for a task id and seed |
| `POST` | `/step` | execute one action and receive observation, reward, done, and info |
| `GET` | `/state` | inspect the current episode state |

Example reset:

```bash
curl -X POST http://127.0.0.1:7860/reset \
  -H "Content-Type: application/json" \
  -d '{"task_id":"easy","seed":11}'
```

Example step:

```bash
curl -X POST http://127.0.0.1:7860/step \
  -H "Content-Type: application/json" \
  -d '{"command":"kubectl -n tron get pods"}'
```

---

## Hackathon Quick Start

```bash
python3 -m venv .venv
. .venv/bin/activate
.venv/bin/pip install -r requirements.txt
./setup.sh

# terminal 1
.venv/bin/python -m tron_openenv.server.app

# terminal 2
export API_BASE_URL=https://api.openai.com/v1
export MODEL_NAME=gpt-5-mini
export HF_TOKEN=$OPENAI_API_KEY
.venv/bin/python inference.py --env-base-url http://127.0.0.1:8000
```

---

## Local Setup

Prerequisites:

- Docker
- `kubectl`
- `k3d`
- Python 3.9+

Bootstrap the local benchmark cluster:

```bash
chmod +x setup.sh cleanup.sh app/test_client.sh
./setup.sh
```

Notes:

- ingress is exposed on `http://127.0.0.1:8080`
- `setup.sh` only pulls images if they are not already present locally
- `setup.sh` also pins `kubectl` to the target `k3d` context before applying manifests
- if you already have different local image tags cached, you can override them with `REDIS_IMAGE=...`, `NGINX_IMAGE=...`, and `PYTHON_IMAGE=...`
- the baseline app can be smoke-tested with `./app/test_client.sh`

Run one scenario manually with the naive baseline:

```bash
.venv/bin/python eval/run_eval.py \
  --agent naive \
  --scenario bad-rollout-wrong-redis-host \
  --seed 11 \
  --output eval/manual-run.jsonl

.venv/bin/python eval/summarize_results.py eval/manual-run.jsonl
```

If you want to inspect the live cluster between steps, run setup first and then use `kubectl` directly against the `tron` namespace.

---

## OpenEnv Server And Baseline Inference

Start the OpenEnv server locally:

```bash
.venv/bin/python -m tron_openenv.server.app
```

By default this listens on `http://127.0.0.1:8000`.

Run the required root inference script:

```bash
export API_BASE_URL=https://api.openai.com/v1
export MODEL_NAME=gpt-5-mini
export HF_TOKEN=$OPENAI_API_KEY
.venv/bin/python inference.py --env-base-url http://127.0.0.1:8000
```

Optional flags:

- `--task easy|medium|hard`
- `--seed 11`
- `--hard-reset`

`inference.py` emits structured stdout logs with `[START]`, `[STEP]`, and `[END]`.

---

## Demo And Evaluation

Run the reviewer-facing scripted demo:

```bash
.venv/bin/python eval/demo.py --scenario service-selector-mismatch --seed 11
```

Run the naive baseline:

```bash
.venv/bin/python eval/run_eval.py --agent naive --output eval/naive-results.jsonl
.venv/bin/python eval/summarize_results.py eval/naive-results.jsonl
```

Run the LLM baseline:

```bash
cp .env.example .env
.venv/bin/python eval/run_eval.py --agent llm --output eval/llm-results.jsonl
```

Offline deterministic example:

```bash
export TRON_LLM_PLAN=$'kubectl -n tron get pods\nkubectl -n tron get configmap app-config -o yaml\nkubectl -n tron get ingress tron-ingress -o yaml'
.venv/bin/python eval/run_eval.py --agent llm --scenario networkpolicy-blocks-nginx-to-redis --seed 13
```

Run the full evaluation suite:

```bash
.venv/bin/python eval/run_eval.py --agent all --output eval/results.jsonl
.venv/bin/python eval/summarize_results.py eval/results.jsonl
.venv/bin/python eval/summarize_results.py eval/results.jsonl --json-out eval/results-summary.json
```

---

## Docker And Hugging Face Spaces

Build the container:

```bash
docker build -t tron-env .
```

Run it locally:

```bash
docker run --rm -p 7860:7860 tron-env
```

The Docker image starts the OpenEnv server and defaults to port `7860`, which matches Hugging Face Spaces.

The container entrypoint also supports remote-cluster secrets:

| var | value |
|---|---|
| `KUBECONFIG_B64` | base64-encoded kubeconfig for the remote k3s cluster |
| `INGRESS_HOST` | remote ingress hostname or IP used for outbound probing |
| `INGRESS_PORT` | remote ingress port |
| `INGRESS_HOST_HEADER` | optional Host header override for the ingress rule |

If those are present, [`scripts/container-entrypoint.sh`](scripts/container-entrypoint.sh) loads them before starting the container command.

For remote EC2 setup, see [`scripts/README.md`](scripts/README.md).

---

## Quality Checks

Run the full local gate:

```bash
make ci
```

Run the container smoke path:

```bash
make docker-smoke
```

Install a pinned official OpenEnv CLI locally:

```bash
make openenv-install
```

Run the local OpenEnv tooling check:

```bash
make openenv-check
```

Behavior:

- if the official `openenv` CLI is installed and exposes `validate`, the repo runs `openenv validate openenv.yaml`
- otherwise it falls back to the local contract test in [`tests/test_openenv_contract.py`](tests/test_openenv_contract.py)
- the pinned OpenEnv reference used by `make openenv-install` is defined in [`Makefile`](Makefile)

---

## E2E Validation

Local unit-style validation:

```bash
PYTHONPYCACHEPREFIX=.pycache .venv/bin/python -m unittest discover -s tests -q
```

Live E2E validation against a disposable cluster:

```bash
TRON_RUN_E2E=1 .venv/bin/python -m unittest tests.test_e2e -q
```

The E2E tests create an isolated `k3d` cluster, inject real incidents, verify degraded black-box behavior, restore the cluster, and assert recovery.

---

## Repository Entrypoints

- benchmark core: [`tron/env.py`](tron/env.py)
- OpenEnv server: [`tron_openenv/server/app.py`](tron_openenv/server/app.py)
- root baseline: [`inference.py`](inference.py)
- Docker entrypoint: [`scripts/container-entrypoint.sh`](scripts/container-entrypoint.sh)
- environment contract: [`openenv.yaml`](openenv.yaml)
