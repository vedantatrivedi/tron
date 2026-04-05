from __future__ import annotations

"""Explicit benchmark environment loop for tron."""

import time
from tron.checks import format_failed_checks
from tron.executor import CommandExecutor
from tron.incident_engine import IncidentEngine
from tron.models import (
    AgentStep,
    BenchmarkConfig,
    EvaluationRecord,
    ObservationBundle,
    ScenarioInstance,
    StepTransition,
)
from tron.observations import collect_observations
from tron.oracle import evaluate_repair, probe_service
from tron.rewards import discriminating_read_bonus, repeated_no_effect_penalty
from tron.runtime_setup import (
    build_baseline_restore_commands,
    build_cluster_env_prefix,
    build_hard_reset_commands,
    run_checked_commands,
)
from tron.sampler import sample_scenario
from tron.scenario_catalog import load_catalog


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

    def _cluster_env_prefix(self) -> str:
        return build_cluster_env_prefix(self.config.cluster)

    def reset_cluster(self) -> None:
        run_checked_commands(
            self.executor,
            build_hard_reset_commands(self.config.cluster),
            timeout=self.config.trusted_timeout_seconds,
            stage="cluster reset",
        )

    def restore_baseline(self) -> None:
        run_checked_commands(
            self.executor,
            build_baseline_restore_commands(self.config.cluster.namespace),
            timeout=self.config.trusted_timeout_seconds,
            stage="baseline restore",
        )

    def _validate_instance_contract(self, instance: ScenarioInstance) -> None:
        activation_failure = format_failed_checks(
            "scenario activation failed: ",
            self.incident_engine.verify_activation(instance),
        )
        if activation_failure:
            raise RuntimeError(activation_failure)
        if not instance.template.requires_service_degradation:
            return
        clue_failure = format_failed_checks(
            "scenario cluster clues missing: ",
            self.incident_engine.verify_cluster_clues(instance),
        )
        if clue_failure:
            raise RuntimeError(clue_failure)

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
            if observation.service_probe.score < 1.0:
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
        self._validate_instance_contract(self.current_instance)

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
            + discriminating_read_bonus(action, result.return_code, result.stdout or "")
            + repeated_no_effect_penalty(
                action,
                observation.service_probe.score,
                self.current_service_score,
                self.steps,
            ),
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
            "parameters": instance.chosen_parameters,
            "recent_changes": instance.recent_changes,
        }
