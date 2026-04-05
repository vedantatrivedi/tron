from __future__ import annotations

"""Explicit benchmark environment loop for tron."""

import time
from dataclasses import asdict

from executor import CommandExecutor
from incident_engine import IncidentEngine
from models import (
    AgentStep,
    BenchmarkConfig,
    EvaluationRecord,
    ObservationBundle,
    ScenarioInstance,
    ScenarioKind,
    StepTransition,
)
from observations import collect_observations
from oracle import evaluate_repair, probe_service
from sampler import sample_scenario
from scenario_catalog import load_catalog


class TronEnvironment:
    def __init__(
        self,
        config: BenchmarkConfig,
        executor: CommandExecutor | None = None,
        catalog: list | None = None,
        incident_engine: IncidentEngine | None = None,
    ) -> None:
        self.config = config
        self.executor = executor or CommandExecutor(cwd=str(config.work_dir))
        self.catalog = catalog or load_catalog()
        self.incident_engine = incident_engine or IncidentEngine(self.executor)
        self.current_instance: ScenarioInstance | None = None
        self.current_observation: ObservationBundle | None = None
        self.current_service_score: float = 0.0
        self.last_reward: float = 0.0
        self.step_number: int = 0
        self.done: bool = False
        self.steps: list[AgentStep] = []

    def _raise_command_failure(self, stage: str, command: str, result) -> None:
        details = result.stderr or result.stdout or "command failed with no output"
        raise RuntimeError(f"{stage} failed for `{command}`: {details}")

    def _command_family(self, command: str) -> str:
        lowered = command.lower()
        if "configmap app-config" in lowered:
            return "app_config"
        if "networkpolicy" in lowered:
            return "network_policy"
        if "service redis" in lowered:
            return "redis_service"
        if "endpoints redis" in lowered:
            return "redis_endpoints"
        if "ingress" in lowered:
            return "ingress"
        if "rollout restart deployment/nginx" in lowered:
            return "restart_nginx"
        if "rollout restart deployment/redis" in lowered:
            return "restart_redis"
        if "deployment nginx" in lowered:
            return "nginx_deployment"
        if "logs" in lowered and "redis-bridge" in lowered:
            return "bridge_logs"
        if (
            ("get pods" in lowered or "get pod" in lowered or "exec " in lowered)
            and "redis_host" in lowered
        ):
            return "live_runtime_env"
        return "other"

    def _discriminating_read_bonus(self, action: str, result) -> float:
        if result.return_code != 0:
            return 0.0
        family = self._command_family(action)
        stdout = result.stdout or ""
        if family == "app_config" and "REDIS_HOST:" in stdout:
            return 0.02
        if family == "redis_service" and "selector:" in stdout:
            return 0.02
        if family == "redis_endpoints" and stdout.strip():
            return 0.02
        if family == "network_policy" and ("kind: NetworkPolicy" in stdout or "items:" in stdout):
            return 0.02
        if family == "nginx_deployment" and stdout.strip():
            return 0.02
        if family == "bridge_logs" and stdout.strip():
            return 0.02
        if family == "live_runtime_env" and stdout.strip():
            return 0.03
        return 0.0

    def _repeated_no_effect_penalty(self, action: str, service_score: float) -> float:
        family = self._command_family(action)
        if family == "other":
            return 0.0
        if service_score != self.current_service_score:
            return 0.0
        recent_same_family = 0
        for previous in reversed(self.steps):
            previous_family = self._command_family(previous.command)
            if previous_family != family:
                break
            if previous.reward > 0:
                break
            recent_same_family += 1
        if recent_same_family == 0:
            return 0.0
        if family in {"restart_nginx", "restart_redis"}:
            return -0.05 * min(recent_same_family, 3)
        if family in {"redis_service", "network_policy"}:
            return -0.05
        return 0.0

    def _cluster_env_prefix(self) -> str:
        return (
            f"CLUSTER_NAME={self.config.cluster.cluster_name} "
            f"INGRESS_HOST={self.config.cluster.ingress_host} "
            f"INGRESS_PORT={self.config.cluster.ingress_port} "
            f"NAMESPACE={self.config.cluster.namespace}"
        )

    def reset_cluster(self) -> None:
        prefix = self._cluster_env_prefix()
        cleanup_command = f"{prefix} bash ./cleanup.sh"
        result = self.executor.run(
            cleanup_command,
            timeout=self.config.trusted_timeout_seconds,
        )
        if result.return_code != 0:
            self._raise_command_failure("cluster cleanup", cleanup_command, result)
        setup_command = f"{prefix} bash ./setup.sh"
        result = self.executor.run(
            setup_command,
            timeout=self.config.trusted_timeout_seconds,
        )
        if result.return_code != 0:
            self._raise_command_failure("cluster setup", setup_command, result)

    def restore_baseline(self) -> None:
        commands = [
            "kubectl apply --validate=false -f manifests/namespace.yaml",
            f"kubectl -n {self.config.cluster.namespace} apply --validate=false -f manifests/configmap.yaml",
            f"kubectl -n {self.config.cluster.namespace} apply --validate=false -f manifests/redis.yaml",
            f"kubectl -n {self.config.cluster.namespace} apply --validate=false -f manifests/nginx.yaml",
            f"kubectl -n {self.config.cluster.namespace} apply --validate=false -f manifests/ingress.yaml",
            f"kubectl -n {self.config.cluster.namespace} apply --validate=false -f manifests/networkpolicy-base.yaml",
            f"kubectl -n {self.config.cluster.namespace} set env deployment/nginx REDIS_HOST-",
            f"kubectl -n {self.config.cluster.namespace} rollout status deployment/redis --timeout=120s",
            f"kubectl -n {self.config.cluster.namespace} rollout status deployment/nginx --timeout=120s",
        ]
        for command in commands:
            result = self.executor.run(command, timeout=self.config.trusted_timeout_seconds)
            if result.return_code != 0:
                self._raise_command_failure("baseline restore", command, result)

    def sample(self, scenario_id: str | None = None, seed: int | None = None) -> ScenarioInstance:
        return sample_scenario(
            self.catalog,
            seed=self.config.random_seed if seed is None else seed,
            scenario_id=scenario_id,
        )

    def inject(self, instance: ScenarioInstance) -> None:
        self.incident_engine.inject(instance)

    def observe(
        self,
        instance: ScenarioInstance,
        step_number: int = 0,
        last_action: str | None = None,
        last_reward: float = 0.0,
    ) -> ObservationBundle:
        service_probe = probe_service(self.config)
        return collect_observations(
            self.executor,
            self.config,
            instance,
            step_number,
            last_action,
            last_reward,
            service_probe,
        )

    def _wait_for_incident_observation(self, instance: ScenarioInstance) -> ObservationBundle:
        if not instance.template.requires_service_degradation:
            return self.observe(instance)
        deadline = time.time() + self.config.trusted_timeout_seconds
        last_observation: ObservationBundle | None = None

        while time.time() < deadline:
            observation = self.observe(instance)
            last_observation = observation
            score = observation.service_probe.score

            if instance.template.kind == ScenarioKind.INGRESS:
                if score < 1.0:
                    return observation
            elif score < 1.0:
                return observation

            time.sleep(self.config.mutation_settle_seconds)

        if last_observation is None:
            raise RuntimeError("failed to collect post-injection observation")
        raise RuntimeError(
            "scenario did not become externally visible: "
            f"score={last_observation.service_probe.score:.2f} "
            f"health={last_observation.service_probe.health_status} "
            f"data={last_observation.service_probe.data_status}"
        )

    def reset(
        self,
        scenario_id: str | None = None,
        seed: int | None = None,
        hard_reset: bool = False,
    ) -> ObservationBundle:
        """Restore baseline, inject a scenario, and return the first observation."""

        if hard_reset:
            self.reset_cluster()
        else:
            self.restore_baseline()
        self.current_instance = self.sample(scenario_id=scenario_id, seed=seed)
        self.inject(self.current_instance)
        activation = self.incident_engine.verify_activation(self.current_instance)
        failed_checks = [check for check in activation if not check.ok]
        if failed_checks:
            raise RuntimeError(
                "scenario activation failed: "
                + ", ".join(f"{check.name} -> {check.details}" for check in failed_checks)
            )

        self.step_number = 0
        self.last_reward = 0.0
        self.done = False
        self.steps = []
        self.current_observation = self._wait_for_incident_observation(self.current_instance)
        self.current_service_score = self.current_observation.service_probe.score
        return self.current_observation

    def step(self, action: str) -> StepTransition:
        """Execute one agent action and return the next observation."""

        if self.current_instance is None:
            raise RuntimeError("reset() must be called before step()")
        if self.done:
            raise RuntimeError("environment is already done")

        result = self.executor.execute_action(
            action,
            timeout=self.config.action_timeout_seconds,
        )
        if self.executor.is_mutating(action) and not result.rejected:
            time.sleep(self.config.mutation_settle_seconds)

        next_step_number = self.step_number + 1
        observation = self.observe(
            self.current_instance,
            step_number=next_step_number,
            last_action=action,
            last_reward=0.0,
        )
        reward = round(
            (observation.service_probe.score - self.current_service_score)
            + result.action_cost
            + self._discriminating_read_bonus(action, result)
            + self._repeated_no_effect_penalty(action, observation.service_probe.score),
            3,
        )
        observation.last_reward = reward

        self.step_number = next_step_number
        self.last_reward = reward
        self.current_service_score = observation.service_probe.score
        self.current_observation = observation
        self.done = (
            observation.service_probe.score >= 1.0
            or self.step_number >= self.config.max_agent_steps
        )

        self.steps.append(
            AgentStep(
                command=action,
                return_code=result.return_code,
                stdout=result.stdout,
                stderr=result.stderr,
                reward=reward,
            )
        )
        return StepTransition(
            observation=observation,
            reward=reward,
            done=self.done,
            service_score=observation.service_probe.score,
            info={
                "rejected": result.rejected,
                "action_cost": result.action_cost,
                "timed_out": result.timed_out,
            },
        )

    def execute_agent(self, commands: list[str]) -> list[AgentStep]:
        if self.current_instance is None:
            raise RuntimeError("reset() must be called before execute_agent()")
        for command in commands[: self.config.max_agent_steps]:
            transition = self.step(command)
            if transition.done:
                break
        return self.steps

    def evaluate(self, instance: ScenarioInstance, steps: list[AgentStep]) -> EvaluationRecord:
        observations = self.current_observation or self.observe(
            instance,
            step_number=self.step_number,
            last_action=self.steps[-1].command if self.steps else None,
            last_reward=self.last_reward,
        )
        return evaluate_repair(
            self.executor,
            self.config,
            instance,
            observations,
            steps,
        )

    def describe_instance(self, instance: ScenarioInstance) -> dict:
        return {
            "scenario_id": instance.template.scenario_id,
            "title": instance.template.title,
            "description": instance.template.description,
            "seed": instance.seed,
            "parameters": asdict(instance).get("chosen_parameters", instance.chosen_parameters),
            "recent_changes": instance.recent_changes,
        }
