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

# tron

`tron` is a live Kubernetes incident benchmark and OpenEnv-style environment for evaluating diagnosis-and-repair agents under partial observability.

The benchmark core runs realistic cluster mutations against a disposable `k3d` or remote k3s cluster. The OpenEnv wrapper exposes a typed HTTP API with:

- `POST /reset`
- `POST /step`
- `GET /state`
- typed Pydantic task, action, observation, reward, and state models
- a root [`inference.py`](inference.py) baseline that uses the OpenAI client

## What the agent sees

Each episode starts with a broken service and a small observation bundle:

- a black-box probe of `/health` and `/data`
- compact summaries of pods, services, deployments, and endpoints
- one recent-change hint
- the previous action and reward

The agent does not automatically get logs, `describe`, or event history. It must spend steps on `kubectl` or `curl` to collect more evidence.

## Official OpenEnv tasks

The submission surface currently exposes three deterministic tasks:

| task | scenario | difficulty | objective |
|---|---|---|---|
| `easy` | `service-selector-mismatch` | easy | Repair service-to-pod wiring so nginx can reach redis again. |
| `medium` | `bad-rollout-wrong-redis-host` | medium | Repair config drift and ensure the serving workload picks up the durable fix. |
| `hard` | `networkpolicy-plus-secondary-drift` | hard | Repair a compound outage spanning both policy and routing drift. |

These are the tasks the root [`inference.py`](inference.py) baseline runs by default.

## Current official baseline

Latest measured OpenEnv baseline run:

- model: `gpt-5-mini`
- seed: `11`
- command: `.venv/bin/python inference.py --env-base-url http://127.0.0.1:8000 --seed 11`

Observed scores:

- `easy` / `service-selector-mismatch`: `oracle_score=0.85`, `steps=12`, `verdict=failure`
- `medium` / `bad-rollout-wrong-redis-host`: `oracle_score=0.50`, `steps=15`, `verdict=failure`
- `hard` / `networkpolicy-plus-secondary-drift`: `oracle_score=0.60`, `steps=18`, `verdict=failure`

This is an honest baseline, not a tuned best-case run. The OpenEnv wrapper is stable and reproducible, but the current model baseline still underperforms on durable repair closure.

## Action, observation, and state spaces

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

## Reward model

Per-step reward is:

`new_service_score - previous_service_score + action_cost`

Action costs:

- `kubectl get`, `describe`, `logs`, `top`, `rollout history`, `curl`: `0.0`
- `kubectl exec`: `-0.02`
- `kubectl apply`, `kubectl set`: `-0.05`
- `kubectl edit`: `-0.08`
- `kubectl rollout restart`: `-0.10`
- `kubectl scale`: `-0.15`
- `kubectl delete`: `-0.30`

The final oracle score combines black-box recovery with scenario-specific repair checks, so workaround recoveries can still fail.

## Scenario catalog

The current benchmark catalog includes 12 scenarios:

- `bad-rollout-wrong-redis-host`
- `configmap-fixed-but-pods-stale`
- `service-selector-mismatch`
- `cpu-limits-too-low`
- `memory-limits-too-low`
- `readiness-probe-too-permissive`
- `networkpolicy-blocks-nginx-to-redis`
- `ingress-path-rewrite-bug`
- `bridge-crashloop-bad-command`
- `deployment-scaled-to-zero`
- `wrong-redis-host-plus-cpu-throttle`
- `networkpolicy-plus-secondary-drift`

## OpenEnv wrapper layout

The benchmark core lives under [`tron/`](tron/). The OpenEnv-facing surface lives under [`tron_openenv/`](tron_openenv/):

- [`tron_openenv/models.py`](tron_openenv/models.py): typed task, action, observation, reward, and state models
- [`tron_openenv/client.py`](tron_openenv/client.py): HTTP client for `reset()`, `step()`, and `state()`
- [`tron_openenv/server/environment.py`](tron_openenv/server/environment.py): adapter from the benchmark core to the official task API
- [`tron_openenv/server/app.py`](tron_openenv/server/app.py): FastAPI server used by the Docker image

## HTTP API

The OpenEnv-style server exposes:

- `GET /` → metadata and task list
- `GET /health` → liveness
- `GET /tasks` → official task catalog
- `POST /reset` → start an episode for a task id and seed
- `POST /step` → execute one action and receive observation, reward, done, and info
- `GET /state` → inspect the current episode state

## Local setup

Prerequisites:

- Docker
- `kubectl`
- `k3d`
- Python 3.9+

Create a virtualenv and install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
.venv/bin/pip install -r requirements.txt
```

Bootstrap the local benchmark cluster:

```bash
chmod +x setup.sh cleanup.sh app/test_client.sh
./setup.sh
```

Notes:

- ingress is exposed on `http://127.0.0.1:8080`
- `setup.sh` only pulls images if they are not already present locally
- the app can be smoke-tested with `./app/test_client.sh`

## Run the OpenEnv server and baseline inference

Start the OpenEnv server locally:

```bash
.venv/bin/python -m tron_openenv.server.app
```

By default this listens on `http://127.0.0.1:8000`.

In another shell, run the required root inference script:

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

## Docker and Hugging Face Spaces

Build the container:

```bash
docker build -t tron-env .
```

Run it locally:

```bash
docker run --rm -p 7860:7860 tron-env
```

The Docker image starts the OpenEnv server and defaults to port `7860`, which matches Hugging Face Spaces.

For a remote EC2 or k3s-backed deployment, the container entrypoint supports:

- `KUBECONFIG_B64`
- `INGRESS_HOST`
- `INGRESS_PORT`
- `INGRESS_HOST_HEADER`

## Quality checks

Run the full local gate:

```bash
make ci
```

Run the container smoke path:

```bash
make docker-smoke
```

Run the local OpenEnv tooling check:

```bash
make openenv-check
```

## Deterministic demo

Run the reviewer-facing scripted demo:

```bash
.venv/bin/python eval/demo.py --scenario service-selector-mismatch --seed 11
```

## Repository entrypoints

- benchmark core: [`tron/env.py`](tron/env.py)
- OpenEnv server: [`tron_openenv/server/app.py`](tron_openenv/server/app.py)
- root baseline: [`inference.py`](inference.py)
- Docker entrypoint: [`scripts/container-entrypoint.sh`](scripts/container-entrypoint.sh)
- environment contract: [`openenv.yaml`](openenv.yaml)
