from __future__ import annotations

"""FastAPI REST server exposing TronEnvironment over HTTP on port 7860."""

import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from tron.models import BenchmarkConfig, ClusterConfig

app = FastAPI(
    title="tron",
    description=(
        "Live Kubernetes incident benchmark. "
        "Drop an agent into a disposable k3d cluster and score whether it repairs the system."
    ),
    version="1.0.0",
)

# Global environment state — single-session for now
_env = None
_config = None


def _get_config() -> BenchmarkConfig:
    return BenchmarkConfig(
        random_seed=int(os.environ.get("TRON_SEED", "0")),
        max_agent_steps=int(os.environ.get("TRON_MAX_STEPS", "12")),
        work_dir=ROOT,
        cluster=ClusterConfig(
            cluster_name=os.environ.get("CLUSTER_NAME", "tron-lab"),
            namespace=os.environ.get("NAMESPACE", "tron"),
            ingress_host=os.environ.get("INGRESS_HOST", "tron.localhost"),
            ingress_port=int(os.environ.get("INGRESS_PORT", "8080")),
        ),
    )


def _observation_to_dict(obs) -> dict:
    return {
        "incident_brief": obs.incident_brief,
        "step_number": obs.step_number,
        "last_action": obs.last_action,
        "last_reward": obs.last_reward,
        "service_probe": {
            "health_status": obs.service_probe.health_status,
            "data_status": obs.service_probe.data_status,
            "http_status": obs.service_probe.http_status,
            "latency_ms": obs.service_probe.latency_ms,
            "score": obs.service_probe.score,
        },
        "cluster_summary": {
            "pods": obs.cluster_summary.pods,
            "services": obs.cluster_summary.services,
            "deployments": obs.cluster_summary.deployments,
            "endpoints": obs.cluster_summary.endpoints,
        },
        "recent_change_hint": obs.recent_change_hint,
    }


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class ResetRequest(BaseModel):
    scenario_id: str | None = None
    seed: int | None = None
    hard_reset: bool = False


class StepRequest(BaseModel):
    action: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/info")
def info() -> dict:
    """Return static environment metadata."""
    from tron.scenario_catalog import load_catalog
    catalog = load_catalog()
    return {
        "name": "tron",
        "description": (
            "Live Kubernetes incident benchmark: diagnose and repair "
            "realistic incidents in a disposable k3d cluster under partial observability."
        ),
        "version": "1.0.0",
        "action_space": {
            "type": "text",
            "allowed_binaries": ["kubectl", "curl"],
            "description": (
                "A single kubectl or curl command. "
                "Shell operators (|, &&, ||, ;) and redirects are not allowed."
            ),
        },
        "observation_space": {
            "type": "structured",
            "fields": {
                "incident_brief": "Short natural-language description of the active incident.",
                "step_number": "Current step index within the episode.",
                "last_action": "The most recent command issued by the agent.",
                "last_reward": "Reward received for the most recent action.",
                "service_probe": {
                    "health_status": "ok | error | timeout | unreachable",
                    "data_status": "ok | error | timeout | unreachable",
                    "http_status": "HTTP status code of the last /data probe, or null.",
                    "latency_ms": "Round-trip latency in ms, or null.",
                    "score": "0.0–1.0 composite service score.",
                },
                "cluster_summary": {
                    "pods": "Compact kubectl get pods output.",
                    "services": "Compact kubectl get services output.",
                    "deployments": "Compact kubectl get deployments output.",
                    "endpoints": "Compact kubectl get endpoints output.",
                },
                "recent_change_hint": "One templated hint about a recent cluster change.",
            },
        },
        "reward": {
            "formula": "service_score_delta + action_cost",
            "action_costs": {
                "kubectl get / describe / logs / top / rollout history / curl": 0.0,
                "kubectl exec": -0.02,
                "kubectl apply / set": -0.05,
                "kubectl edit": -0.08,
                "kubectl rollout restart": -0.10,
                "kubectl scale": -0.15,
                "kubectl delete": -0.30,
                "rejected action": -0.05,
            },
        },
        "max_steps": 12,
        "scenarios": [
            {
                "id": t.id,
                "title": t.title,
                "kind": t.kind.value,
                "difficulty": t.difficulty,
                "description": t.user_visible_symptom,
            }
            for t in catalog
        ],
    }


@app.post("/reset")
def reset(body: ResetRequest) -> dict:
    """Reset the environment and return the initial observation."""
    global _env, _config
    try:
        from tron.env import TronEnvironment

        _config = _get_config()
        _env = TronEnvironment(_config)
        obs = _env.reset(
            scenario_id=body.scenario_id,
            seed=body.seed,
            hard_reset=body.hard_reset,
        )
        instance = _env.current_instance
        return {
            "scenario_id": instance.template.scenario_id,
            "scenario_title": instance.template.title,
            "difficulty": instance.template.difficulty,
            "seed": instance.seed,
            "chosen_parameters": instance.chosen_parameters,
            "recent_changes": instance.recent_changes,
            "observation": _observation_to_dict(obs),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/step")
def step(body: StepRequest) -> dict:
    """Execute one action and return the next observation + reward."""
    if _env is None:
        raise HTTPException(status_code=400, detail="Call /reset first.")
    try:
        transition = _env.step(body.action)
        return {
            "observation": _observation_to_dict(transition.observation),
            "reward": transition.reward,
            "done": transition.done,
            "service_score": transition.service_score,
            "info": transition.info,
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/observation")
def observation() -> dict:
    """Return the current observation without advancing the environment."""
    if _env is None or _env.current_observation is None:
        raise HTTPException(status_code=400, detail="Call /reset first.")
    return {
        "observation": _observation_to_dict(_env.current_observation),
        "step_number": _env.step_number,
        "done": _env.done,
        "service_score": _env.current_service_score,
    }


@app.post("/evaluate")
def evaluate() -> dict:
    """Run the oracle and return the final repair score for the current episode."""
    if _env is None or _env.current_instance is None:
        raise HTTPException(status_code=400, detail="Call /reset first.")
    try:
        record = _env.evaluate(_env.current_instance, _env.steps)
        return {
            "verdict": record.verdict.value,
            "score": record.score,
            "summary": record.summary,
            "checks": [
                {"name": c.name, "ok": c.ok, "details": c.details}
                for c in record.checks
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
def health() -> dict:
    """API liveness probe."""
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
