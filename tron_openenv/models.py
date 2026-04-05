from __future__ import annotations

"""Typed OpenEnv-facing models for the tron environment."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ServiceProbeView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    health_status: str
    data_status: str
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    score: float = Field(ge=0.0, le=1.0)


class ClusterSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pods: str
    services: str
    deployments: str
    endpoints: str


class TronTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    title: str
    difficulty: str
    scenario_id: str
    description: str
    default_seed: int
    max_agent_steps: int


class TronAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)


class TronReward(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float = Field(ge=-1.0, le=1.0)


class TronObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    scenario_id: str
    step_count: int = Field(ge=0)
    incident_brief: str
    last_action: Optional[str] = None
    last_reward: float
    service_probe: ServiceProbeView
    cluster_summary: ClusterSummaryView
    recent_change_hint: str
    done: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = "easy"
    seed: Optional[int] = None
    hard_reset: bool = False


class ResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TronTask
    observation: TronObservation


class StepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: TronObservation
    reward: TronReward
    done: bool
    info: dict[str, Any] = Field(default_factory=dict)


class TronState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: Optional[str] = None
    task: Optional[TronTask] = None
    scenario_id: Optional[str] = None
    seed: Optional[int] = None
    step_count: int = Field(ge=0)
    cumulative_reward: float
    done: bool
    last_action: Optional[str] = None
    last_reward: float = 0.0
    service_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    oracle_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    oracle_verdict: Optional[str] = None
    oracle_summary: Optional[str] = None
