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

This is a benchmark, not a product. The goal is to measure whether an agent can diagnose and repair realistic cluster incidents under pressure, not whether it can explain the fault elegantly.

The benchmark core runs deterministic mutations against a disposable `k3d` or remote k3s cluster. The OpenEnv wrapper exposes a typed HTTP API with:

- `POST /reset`
- `POST /step`
- `GET /state`
- typed Pydantic task, action, observation, reward, and state models
- a root [`inference.py`](inference.py) baseline that uses the OpenAI client

## Why it exists

Most Kubernetes benchmarks stop at code generation or offline reasoning. `tron` focuses on the harder loop:

- a live cluster is already broken
- observability is partial by default
- recent changes are hints, not diagnoses
- the agent must choose commands under an action-cost budget
- success is judged by black-box service recovery plus durable repair checks

That makes it useful for evaluating tool-using incident-response agents instead of static infrastructure trivia.

## Architecture overview

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

## Benchmark status

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

## Observability philosophy

The default observation is intentionally small:

- incident brief
- current step number
- last action and reward
- black-box probe of `/health` and `/data`
- lightweight summaries of pods, services, deployments, and endpoints
- one recent-change hint

The agent does not automatically get:

- full logs
- full `kubectl describe`
- full event history
- direct diagnosis text

If the agent wants more, it must spend a turn on `kubectl` or `curl`.

## Oracle philosophy

The oracle behaves like a black-box SLI evaluator. It checks reachability, HTTP status, latency, and the difference between `/health` and `/data`. It does not diagnose root cause.

Service score buckets:

- `1.0`: healthy
- `0.7`: `/health` works but data path is degraded
- `0.4`: reachable with major errors
- `0.1`: timeout
- `0.0`: unreachable

Final evaluation combines the black-box score with explicit repair checks attached to the selected scenario.

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

Recommended demo scenarios:

- `bad-rollout-wrong-redis-host`
- `networkpolicy-blocks-nginx-to-redis`
- `wrong-redis-host-plus-cpu-throttle`

Those three are also the default scenarios in [`eval/seeds.yaml`](eval/seeds.yaml).

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
- `setup.sh` also pins `kubectl` to the target `k3d` context before applying manifests
- if you already have different local image tags cached, you can override them with `REDIS_IMAGE=...`, `NGINX_IMAGE=...`, and `PYTHON_IMAGE=...`
- the baseline app can be smoke-tested with `./app/test_client.sh`

## Run one scenario manually

Run a single seeded scenario with the naive baseline:

```bash
.venv/bin/python eval/run_eval.py \
  --agent naive \
  --scenario bad-rollout-wrong-redis-host \
  --seed 11 \
  --output eval/manual-run.jsonl
```

Summarize that run:

```bash
.venv/bin/python eval/summarize_results.py eval/manual-run.jsonl
```

If you want to inspect the live cluster between steps, run setup first and then use `kubectl` directly against the `tron` namespace.

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

## Run the evaluator demo

Run a deterministic reviewer-facing demo with scripted repair steps:

```bash
.venv/bin/python eval/demo.py --scenario service-selector-mismatch --seed 11
```

This prints:

- the live incident reset
- each demo step intent and command
- black-box recovery progress
- final oracle verdict
- a compact JSON demo summary

## Run the naive baseline

```bash
.venv/bin/python eval/run_eval.py --agent naive --output eval/naive-results.jsonl
.venv/bin/python eval/summarize_results.py eval/naive-results.jsonl
```

The naive baseline is intentionally weak. It cycles through a costly restart-and-reapply playbook and serves as a floor, not a serious incident responder.

## Run the LLM baseline

The LLM baseline emits exactly one `kubectl` or `curl` command per turn. It is provider-swappable and supports:

- OpenAI-compatible chat completions via `OPENAI_API_KEY`, `OPENAI_MODEL`, and optional `OPENAI_BASE_URL`
- Anthropic messages via `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, and optional `ANTHROPIC_BASE_URL`
- offline fallback via `TRON_LLM_PLAN`, a newline-separated command list

You can configure those through a local `.env` file. A starter template is provided in [`.env.example`](.env.example) and the real `.env` is ignored by git.

Default cheap model choices:

- OpenAI: `gpt-5-mini`
- Anthropic: `claude-3-haiku-20240307`

Quick start:

```bash
cp .env.example .env
```

OpenAI example:

```bash
.venv/bin/python eval/run_eval.py --agent llm --output eval/llm-results.jsonl
```

Offline deterministic example:

```bash
export TRON_LLM_PLAN=$'kubectl -n tron get pods\nkubectl -n tron get configmap app-config -o yaml\nkubectl -n tron get ingress tron-ingress -o yaml'
.venv/bin/python eval/run_eval.py --agent llm --scenario networkpolicy-blocks-nginx-to-redis --seed 13
```

## Run the evaluation suite

Run both baseline agents across the default demo scenarios:

```bash
.venv/bin/python eval/run_eval.py --agent all --output eval/results.jsonl
.venv/bin/python eval/summarize_results.py eval/results.jsonl
```

Write a machine-readable benchmark report at the same time:

```bash
.venv/bin/python eval/summarize_results.py eval/results.jsonl --json-out eval/results-summary.json
```

The default seed plan is defined in [`eval/seeds.yaml`](eval/seeds.yaml). Each JSONL row includes:

- agent name
- scenario id and seed
- chosen randomized parameters
- per-step command log
- step rewards and service scores
- final oracle verdict and score

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

The container entrypoint also supports remote-cluster secrets for Docker or Hugging Face style runtimes:

- `KUBECONFIG_B64`: base64-encoded kubeconfig, decoded to `KUBECONFIG`
- `INGRESS_HOST`: remote ingress hostname or IP used for outbound probing
- `INGRESS_PORT`: remote ingress port
- `INGRESS_HOST_HEADER`: optional HTTP Host header override for the ingress rule (defaults to `tron.localhost`)

If those are present, [`scripts/container-entrypoint.sh`](scripts/container-entrypoint.sh) loads them before starting the container command.

## Quality checks

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

## E2E validation

Local unit-style validation:

```bash
PYTHONPYCACHEPREFIX=.pycache .venv/bin/python -m unittest discover -s tests -q
```

Live E2E validation against a disposable cluster:

```bash
TRON_RUN_E2E=1 .venv/bin/python -m unittest tests.test_e2e -q
```

The E2E tests create an isolated `k3d` cluster, inject real incidents, verify degraded black-box behavior, restore the cluster, and assert recovery.

## Repository entrypoints

- benchmark core: [`tron/env.py`](tron/env.py)
- OpenEnv server: [`tron_openenv/server/app.py`](tron_openenv/server/app.py)
- root baseline: [`inference.py`](inference.py)
- Docker entrypoint: [`scripts/container-entrypoint.sh`](scripts/container-entrypoint.sh)
- environment contract: [`openenv.yaml`](openenv.yaml)
