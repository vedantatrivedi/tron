from __future__ import annotations

"""OpenEnv-compatible server adapter for the tron benchmark core."""

import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from tron.env import TronEnvironment
from tron.models import BenchmarkConfig, ClusterConfig, ObservationBundle, StepTransition
from tron_openenv.models import (
    ClusterSummaryView,
    ResetRequest,
    ResetResponse,
    ServiceProbeView,
    StepResponse,
    TronAction,
    TronObservation,
    TronReward,
    TronState,
    TronTask,
)


ROOT = Path(__file__).resolve().parents[2]

TASK_SCENARIO_IDS: dict[str, str] = {
    "easy": "service-selector-mismatch",
    "medium": "bad-rollout-wrong-redis-host",
    "hard": "networkpolicy-plus-secondary-drift",
}

TASKS: dict[str, TronTask] = {
    "easy": TronTask(
        id="easy",
        difficulty="easy",
        default_seed=11,
        max_agent_steps=12,
    ),
    "medium": TronTask(
        id="medium",
        difficulty="medium",
        default_seed=13,
        max_agent_steps=15,
    ),
    "hard": TronTask(
        id="hard",
        difficulty="hard",
        default_seed=17,
        max_agent_steps=18,
    ),
}


def _build_cluster_config() -> ClusterConfig:
    ingress_host_header = os.getenv("INGRESS_HOST_HEADER", "tron.localhost")
    ingress_endpoint_host = os.getenv("INGRESS_URL_HOST") or os.getenv("INGRESS_HOST")
    if ingress_endpoint_host in {"", "tron.localhost"}:
        ingress_endpoint_host = None

    return ClusterConfig(
        cluster_name=os.getenv("TRON_CLUSTER_NAME", "tron-lab"),
        namespace=os.getenv("TRON_NAMESPACE", "tron"),
        kubeconfig_path=os.getenv("KUBECONFIG"),
        ingress_host=ingress_host_header,
        ingress_port=int(os.getenv("INGRESS_PORT", "8080")),
        ingress_url_host=ingress_endpoint_host,
    )


def _build_config(max_agent_steps: int) -> BenchmarkConfig:
    return BenchmarkConfig(
        random_seed=0,
        max_agent_steps=max_agent_steps,
        work_dir=ROOT,
        cluster=_build_cluster_config(),
    )


class TronOpenEnvService:
    def __init__(self, env: TronEnvironment | None = None, tasks: dict[str, TronTask] | None = None) -> None:
        self.tasks = tasks or TASKS
        self.env = env or TronEnvironment(_build_config(self.tasks["easy"].max_agent_steps))
        self.lock = Lock()
        self.current_task: TronTask | None = None
        self.current_seed: int | None = None
        self.episode_id: str | None = None
        self.cumulative_reward: float = 0.0
        self.last_evaluation = None

    def list_tasks(self) -> list[TronTask]:
        return [self.tasks[key] for key in ("easy", "medium", "hard")]

    def _require_task(self, task_id: str) -> TronTask:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task_id: {task_id}") from exc

    def _observation_to_model(self, observation: ObservationBundle, done: bool) -> TronObservation:
        if self.current_task is None or self.env.current_instance is None:
            raise RuntimeError("task state is not initialized")
        return TronObservation(
            task_id=self.current_task.id,
            step_count=observation.step_number,
            incident_brief=observation.incident_brief,
            last_action=observation.last_action,
            last_reward=observation.last_reward,
            service_probe=ServiceProbeView(
                health_status=observation.service_probe.health_status,
                data_status=observation.service_probe.data_status,
                http_status=observation.service_probe.http_status,
                latency_ms=observation.service_probe.latency_ms,
                score=observation.service_probe.score,
            ),
            cluster_summary=ClusterSummaryView(
                pods=observation.cluster_summary.pods,
                services=observation.cluster_summary.services,
                deployments=observation.cluster_summary.deployments,
                endpoints=observation.cluster_summary.endpoints,
            ),
            recent_change_hint=observation.recent_change_hint,
            done=done,
            metadata={
                "difficulty": self.current_task.difficulty,
            },
        )

    def reset(self, request: ResetRequest) -> ResetResponse:
        with self.lock:
            task = self._require_task(request.task_id)
            seed = request.seed if request.seed is not None else task.default_seed
            self.env.config.max_agent_steps = task.max_agent_steps
            observation = self.env.reset(
                scenario_id=TASK_SCENARIO_IDS[task.id],
                seed=seed,
                hard_reset=request.hard_reset,
            )
            self.current_task = task
            self.current_seed = seed
            self.episode_id = uuid4().hex
            self.cumulative_reward = 0.0
            self.last_evaluation = None
            return ResetResponse(
                task=task,
                observation=self._observation_to_model(observation, done=False),
            )

    def step(self, action: TronAction) -> StepResponse:
        with self.lock:
            if self.current_task is None or self.env.current_instance is None:
                raise RuntimeError("reset() must be called before step()")

            transition: StepTransition = self.env.step(action.command)
            self.cumulative_reward = round(self.cumulative_reward + transition.reward, 3)
            done = transition.done
            info = dict(transition.info)
            last_step = self.env.steps[-1]
            info.update(
                {
                    "command": last_step.command,
                    "return_code": last_step.return_code,
                    "stdout": last_step.stdout,
                    "stderr": last_step.stderr,
                }
            )

            if transition.done:
                evaluation = self.env.evaluate(self.env.current_instance, self.env.steps)
                self.last_evaluation = evaluation
                info.update(
                    {
                        "oracle_score": evaluation.score,
                        "oracle_verdict": evaluation.verdict.value,
                        "oracle_summary": evaluation.summary,
                        "repair_checks": [check.__dict__ for check in evaluation.checks],
                    }
                )
                if (
                    transition.service_score >= 1.0
                    and evaluation.verdict.value != "success"
                    and self.env.step_number < self.env.config.max_agent_steps
                ):
                    self.env.done = False
                    done = False
                    info["repair_complete"] = False
                else:
                    info["repair_complete"] = evaluation.verdict.value == "success"

            return StepResponse(
                observation=self._observation_to_model(transition.observation, done=done),
                reward=TronReward(value=transition.reward),
                done=done,
                info=info,
            )

    def state(self) -> TronState:
        with self.lock:
            service_score = (
                self.env.current_observation.service_probe.score if self.env.current_observation else None
            )
            return TronState(
                episode_id=self.episode_id,
                task=self.current_task,
                seed=self.current_seed,
                step_count=self.env.step_number,
                cumulative_reward=self.cumulative_reward,
                done=self.env.done,
                last_action=self.env.current_observation.last_action if self.env.current_observation else None,
                last_reward=self.env.last_reward,
                service_score=service_score,
                oracle_score=self.last_evaluation.score if self.last_evaluation else None,
                oracle_verdict=self.last_evaluation.verdict.value if self.last_evaluation else None,
                oracle_summary=self.last_evaluation.summary if self.last_evaluation else None,
            )
