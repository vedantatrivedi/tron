# tron

`tron` is a live Kubernetes incident benchmark. It drops an agent into a disposable `k3d` cluster, hides most of the root-cause state, exposes only black-box service probes plus lightweight cluster summaries, and scores whether the agent repaired the system before the step budget runs out.

This is a benchmark, not a product. The point is to measure diagnosis-and-repair behavior under realistic operational pressure, not to provide a polished management plane.

## Why it exists

Most Kubernetes benchmarks stop at code generation or offline reasoning. `tron` focuses on the harder loop:

- a live cluster is already broken
- observability is partial by default
- recent changes are only hints, not direct diagnoses
- the agent must choose commands under an action-cost budget
- success is judged by black-box service recovery, not by explaining the fault

That makes it useful for comparing tool-using agents on incident response rather than static infrastructure trivia.

## Architecture overview

`tron` has five main layers:

- Runtime app: a small two-tier app with `nginx` in front of a Redis-backed sidecar path. `/health` is intentionally shallow and `/data` exercises the backend path.
- Scenario catalog: `tron/scenario_catalog.py` defines single-root-cause and compound incident templates, plus seeded parameter variation.
- Incident engine: `tron/incident_engine.py` applies deterministic cluster mutations and verifies that the intended fault activated.
- Environment loop: `tron/env.py` handles `reset()`, `step(action)`, reward computation, and termination.
- Oracle and eval: `tron/oracle.py`, `eval/run_eval.py`, and `eval/summarize_results.py` score black-box recovery and summarize agent behavior.

## Scenario catalog

The current catalog includes 12 benchmark scenarios:

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

Exactly three recommended demo scenarios:

- `bad-rollout-wrong-redis-host`
- `networkpolicy-blocks-nginx-to-redis`
- `wrong-redis-host-plus-cpu-throttle`

Those three are also the default scenarios in [eval/seeds.yaml](/Users/vedantatrivedi/codex-projects/tron/eval/seeds.yaml).

## Benchmark status

Current strong scenarios for the structured smart baseline:

- `bad-rollout-wrong-redis-host`
- `configmap-fixed-but-pods-stale`
- `service-selector-mismatch`
- `readiness-probe-too-permissive`
- `networkpolicy-blocks-nginx-to-redis`
- `ingress-path-rewrite-bug`
- `networkpolicy-plus-secondary-drift`

Current weak scenarios:

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

Final eval combines the black-box score with explicit repair checks attached to the selected scenario.

## Reward model

Per-step reward is:

`new_service_score - previous_service_score + action_cost`

Cheap diagnostics are free. Destructive actions are penalized:

- `kubectl get`, `describe`, `logs`, `top`, `rollout history`, `curl`: `0.0`
- `kubectl exec`: `-0.02`
- `kubectl apply`, `kubectl set`: `-0.05`
- `kubectl edit`: `-0.08`
- `kubectl rollout restart`: `-0.10`
- `kubectl scale`: `-0.15`
- `kubectl delete`: `-0.30`

This pushes agents toward evidence-gathering before broad repair actions.

## Setup

Host prerequisites:

- Docker
- `kubectl`
- `k3d`
- Python 3.9+

Create a virtualenv and install Python dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
.venv/bin/pip install -r requirements.txt
```

Bootstrap the benchmark cluster:

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

If you want to inspect the live cluster between steps, run the setup first and then use `kubectl` directly against the `tron` namespace.

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

You can configure those through a local `.env` file. A starter template is provided in [/.env.example](/Users/vedantatrivedi/codex-projects/tron/.env.example) and the real `/.env` is ignored by git.

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

The default seed plan is defined in [eval/seeds.yaml](/Users/vedantatrivedi/codex-projects/tron/eval/seeds.yaml). Each JSONL row includes:

- agent name
- scenario id and seed
- chosen randomized parameters
- per-step command log
- step rewards and service scores
- final oracle verdict and score

## E2E validation

Local unit-style validation:

```bash
PYTHONPYCACHEPREFIX=.pycache .venv/bin/python -m unittest discover -s tests -q
```

Or run the full local repo check bundle:

```bash
make ci
```

Containerized local quality smoke:

```bash
make docker-smoke
```

The container entrypoint also supports remote-cluster secrets for Docker or Hugging Face style runtimes:

- `KUBECONFIG_B64`: base64-encoded kubeconfig, decoded to `KUBECONFIG`
- `INGRESS_HOST`: remote ingress hostname or IP
- `INGRESS_PORT`: remote ingress port

If those are present, [scripts/container-entrypoint.sh](/Users/vedantatrivedi/codex-projects/tron/scripts/container-entrypoint.sh) loads them before starting the container command.

Install a pinned official OpenEnv CLI locally:

```bash
make openenv-install
```

Run the local OpenEnv check:

```bash
make openenv-check
```

Behavior:

- if the official `openenv` CLI is installed and exposes `validate`, the repo runs `openenv validate openenv.yaml`
- otherwise it falls back to the local contract test in [tests/test_openenv_contract.py](/Users/vedantatrivedi/codex-projects/tron/tests/test_openenv_contract.py)
- the pinned OpenEnv reference used by `make openenv-install` is defined in [Makefile](/Users/vedantatrivedi/codex-projects/tron/Makefile)

Live E2E validation against a disposable cluster:

```bash
TRON_RUN_E2E=1 .venv/bin/python -m unittest tests.test_e2e -q
```

The E2E tests create an isolated `k3d` cluster, inject real incidents, verify degraded black-box behavior, restore the cluster, and assert recovery.

GitHub Actions runs the same local quality gate on pushes to `main` and on pull requests:

- `unittest` discovery
- Python compile checks
- shell syntax validation for the repo scripts
