from __future__ import annotations

"""Explicit benchmark environment loop for tron."""

import logging
import time
from tron.checks import format_failed_checks

logger = logging.getLogger("tron.env")
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
    build_runtime_override_probe_command,
    command_output_indicates_change,
    deployment_changed_from_apply,
    run_checked_commands,
)
from tron.sampler import sample_scenario
from tron.scenario_catalog import load_catalog

_CLUSTER_UNREACHABLE_FRAGMENTS = (
    "couldn't get current server api group list",
    "the connection to the server",
    "connect: connection refused",
    "no configuration has been provided",
    "current-context is not set",
)


def _check_signature(check) -> tuple[tuple[str, ...], str, str]:
    return tuple(check.command), check.success_substring, check.match_mode


def _is_cluster_unreachable_error(message: str) -> bool:
    lowered = message.lower()
    return any(fragment in lowered for fragment in _CLUSTER_UNREACHABLE_FRAGMENTS)


def _is_rollout_status_command(command: str) -> bool:
    stripped = command.strip()
    return stripped.startswith("kubectl ") and " rollout status " in stripped


class TronEnvironment:
    def __init__(
        self,
        config: BenchmarkConfig,
        executor: CommandExecutor | None = None,
        catalog: list | None = None,
        incident_engine: IncidentEngine | None = None,
    ) -> None:
        self.config = config
        self.executor = executor or CommandExecutor(
            cwd=str(config.work_dir),
            kubeconfig_path=config.cluster.kubeconfig_path,
        )
        self.catalog = catalog or load_catalog()
        self.incident_engine = incident_engine or IncidentEngine(self.executor)
        self.current_instance: ScenarioInstance | None = None
        self.current_observation: ObservationBundle | None = None
        self.current_service_score: float = 0.0
        self.last_reward: float = 0.0
        self.step_number: int = 0
        self.done: bool = False
        self.steps: list[AgentStep] = []
        self._needs_runtime_override_cleanup: bool = False

    def _cluster_env_prefix(self) -> str:
        return build_cluster_env_prefix(self.config.cluster)

    def reset_cluster(self) -> None:
        logger.info("[setup] hard reset: running cleanup.sh + setup.sh")
        run_checked_commands(
            self.executor,
            build_hard_reset_commands(self.config.cluster),
            timeout=self.config.trusted_timeout_seconds,
            stage="cluster reset",
        )
        self._needs_runtime_override_cleanup = False
        logger.info("[setup] hard reset complete")

    def restore_baseline(self) -> None:
        logger.info("[setup] restoring baseline manifests (namespace=%s)", self.config.cluster.namespace)
        namespace = self.config.cluster.namespace
        trusted_timeout = self.config.trusted_timeout_seconds
        commands = build_baseline_restore_commands(
            namespace,
            rollout_timeout_seconds=self.config.rollout_status_timeout_seconds,
        )

        namespace_result = self.executor.run(commands[0], timeout=trusted_timeout)
        if namespace_result.return_code != 0:
            details = namespace_result.stderr or namespace_result.stdout or "command failed with no output"
            raise RuntimeError(f"baseline restore failed for `{commands[0]}`: {details}")

        apply_result = self.executor.run(commands[1], timeout=trusted_timeout)
        if apply_result.return_code != 0:
            details = apply_result.stderr or apply_result.stdout or "command failed with no output"
            raise RuntimeError(f"baseline restore failed for `{commands[1]}`: {details}")

        changed_deployments = {
            name
            for name in ("redis", "nginx")
            if deployment_changed_from_apply(apply_result.stdout, name)
        }

        if self._needs_runtime_override_cleanup:
            override_probe = build_runtime_override_probe_command(namespace)
            probe_result = self.executor.run(override_probe, timeout=trusted_timeout)
            if probe_result.return_code != 0:
                details = probe_result.stderr or probe_result.stdout or "command failed with no output"
                raise RuntimeError(f"baseline restore failed for `{override_probe}`: {details}")

            if "REDIS_HOST" in (probe_result.stdout or "").split():
                clear_result = self.executor.run(commands[2], timeout=trusted_timeout)
                if clear_result.return_code != 0:
                    details = clear_result.stderr or clear_result.stdout or "command failed with no output"
                    raise RuntimeError(f"baseline restore failed for `{commands[2]}`: {details}")
                if command_output_indicates_change(clear_result.stdout or clear_result.stderr):
                    changed_deployments.add("nginx")

        for deployment_name in ("redis", "nginx"):
            if deployment_name not in changed_deployments:
                continue
            rollout_command = (
                f"kubectl -n {namespace} rollout status deployment/{deployment_name} "
                f"--timeout={self.config.rollout_status_timeout_seconds}s"
            )
            rollout_result = self.executor.run(rollout_command, timeout=trusted_timeout)
            if rollout_result.return_code != 0:
                details = rollout_result.stderr or rollout_result.stdout or "command failed with no output"
                raise RuntimeError(f"baseline restore failed for `{rollout_command}`: {details}")
        self._needs_runtime_override_cleanup = False
        logger.info("[setup] baseline restore complete")

    def _validate_instance_contract(self, instance: ScenarioInstance) -> None:
        logger.info("[setup] validating activation checks for scenario=%s", instance.template.scenario_id)
        activation_failure = format_failed_checks(
            "scenario activation failed: ",
            self.incident_engine.verify_activation(instance),
        )
        if activation_failure:
            logger.error("[setup] activation FAILED: %s", activation_failure)
            raise RuntimeError(activation_failure)
        logger.info("[setup] activation checks passed")
        if not instance.template.requires_service_degradation:
            return
        activation_signatures = {_check_signature(check) for check in instance.template.activation_checks}
        if instance.template.cluster_clue_checks:
            clue_checks = [
                check
                for check in instance.template.cluster_clue_checks
                if _check_signature(check) not in activation_signatures
            ]
        else:
            clue_checks = []
        if not clue_checks:
            logger.info("[setup] cluster clue checks reused activation evidence")
            return
        clue_failure = format_failed_checks(
            "scenario cluster clues missing: ",
            self.incident_engine.verify_cluster_clues(instance, clue_checks=clue_checks),
        )
        if clue_failure:
            logger.error("[setup] cluster clue checks FAILED: %s", clue_failure)
            raise RuntimeError(clue_failure)
        logger.info("[setup] cluster clue checks passed")

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
        include_cluster_summary: bool = True,
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
            include_cluster_summary=include_cluster_summary,
        )

    def _wait_for_incident_observation(self, instance: ScenarioInstance) -> ObservationBundle:
        include_cluster_summary = not self.config.skip_reset_cluster_summary
        if not instance.template.requires_service_degradation:
            obs = self.observe(instance, include_cluster_summary=include_cluster_summary)
            logger.info(
                "[setup] initial observation score=%.2f health=%s data=%s",
                obs.service_probe.score,
                obs.service_probe.health_status,
                obs.service_probe.data_status,
            )
            return obs
        logger.info("[setup] waiting for incident to become externally visible...")
        deadline = time.time() + self.config.trusted_timeout_seconds
        transient_deadline: float | None = None
        last_observation: ObservationBundle | None = None

        while time.time() < deadline:
            observation = self.observe(instance, include_cluster_summary=include_cluster_summary)
            last_observation = observation
            logger.info(
                "[setup] probe score=%.2f health=%s data=%s",
                observation.service_probe.score,
                observation.service_probe.health_status,
                observation.service_probe.data_status,
            )
            if observation.service_probe.score < 1.0:
                if observation.service_probe.health_status == "ok":
                    return observation
                if transient_deadline is None:
                    settle_interval = max(self.config.mutation_settle_seconds, 0.1)
                    transient_deadline = time.time() + max(
                        settle_interval,
                        self.config.transient_probe_wait_seconds,
                    )
                    logger.info(
                        "[setup] incident is externally visible but health is still settling; waiting briefly for steady-state symptoms"
                    )
                elif time.time() >= transient_deadline:
                    return observation

            time.sleep(max(self.config.mutation_settle_seconds, 0.1))

        if last_observation is None:
            raise RuntimeError("failed to collect post-injection observation")
        raise RuntimeError(
            "scenario did not become externally visible: "
            f"score={last_observation.service_probe.score:.2f} "
            f"health={last_observation.service_probe.health_status} "
            f"data={last_observation.service_probe.data_status}"
        )

    def _observe_after_rollout_status(
        self,
        instance: ScenarioInstance,
        step_number: int,
        action: str,
    ) -> ObservationBundle:
        observation = self.observe(
            instance,
            step_number=step_number,
            last_action=action,
            last_reward=0.0,
        )
        if observation.service_probe.score >= 1.0:
            return observation

        settle_interval = max(self.config.mutation_settle_seconds, 0.5)
        deadline = time.time() + max(settle_interval * 4, self.config.transient_probe_wait_seconds)
        logger.info(
            "[step %d] rollout complete but service still degraded; waiting briefly for black-box recovery",
            step_number,
        )
        while time.time() < deadline:
            time.sleep(settle_interval)
            observation = self.observe(
                instance,
                step_number=step_number,
                last_action=action,
                last_reward=0.0,
            )
            if observation.service_probe.score >= 1.0:
                break
        return observation

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
            try:
                self.restore_baseline()
            except RuntimeError as exc:
                if not _is_cluster_unreachable_error(str(exc)):
                    raise
                logger.warning(
                    "[setup] baseline restore failed because the cluster is unreachable; "
                    "retrying with hard reset: %s",
                    exc,
                )
                self.reset_cluster()
        self.current_instance = self.sample(scenario_id=scenario_id, seed=seed)
        logger.info(
            "[setup] injecting scenario=%s seed=%d difficulty=%s",
            self.current_instance.template.scenario_id,
            self.current_instance.seed,
            self.current_instance.template.difficulty,
        )
        for hint in self.current_instance.recent_changes:
            logger.info("[setup] recent_change: %s", hint)
        self.inject(self.current_instance)
        logger.info("[setup] injection complete, running activation checks")
        if self.config.skip_reset_validation:
            logger.info("[setup] skipping reset validation checks per config")
        else:
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
        if result.rejected:
            logger.warning("[step %d] REJECTED: %s", self.step_number + 1, action)
        elif result.timed_out:
            logger.warning("[step %d] TIMEOUT: %s", self.step_number + 1, action)
        elif result.return_code != 0:
            detail = (result.stderr or result.stdout or "").replace("\n", " ")[:120]
            logger.warning("[step %d] rc=%d: %s | %s", self.step_number + 1, result.return_code, action, detail)
        else:
            logger.info("[step %d] rc=0: %s", self.step_number + 1, action)
        if self.executor.is_mutating(action) and not result.rejected:
            self._needs_runtime_override_cleanup = True
            time.sleep(self.config.mutation_settle_seconds)

        next_step_number = self.step_number + 1
        if (
            not result.rejected
            and not result.timed_out
            and result.return_code == 0
            and _is_rollout_status_command(action)
        ):
            observation = self._observe_after_rollout_status(
                self.current_instance,
                step_number=next_step_number,
                action=action,
            )
        else:
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

        logger.info(
            "[step %d] reward=%+.3f score=%.2f health=%s data=%s",
            self.step_number,
            reward,
            observation.service_probe.score,
            observation.service_probe.health_status,
            observation.service_probe.data_status,
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
        logger.info("[evaluate] running oracle for scenario=%s steps=%d", instance.template.scenario_id, len(steps))
        observations = self.current_observation or self.observe(
            instance,
            step_number=self.step_number,
            last_action=self.steps[-1].command if self.steps else None,
            last_reward=self.last_reward,
        )
        record = evaluate_repair(
            self.executor,
            self.config,
            instance,
            observations,
            steps,
        )
        logger.info("[evaluate] verdict=%s oracle=%.2f | %s", record.verdict.value, record.score, record.summary)
        for check in record.checks:
            mark = "PASS" if check.ok else "FAIL"
            logger.info("[evaluate] check %s %s: %s", mark, check.name, check.details)
        return record

    def describe_instance(self, instance: ScenarioInstance) -> dict:
        return {
            "scenario_id": instance.template.scenario_id,
            "title": instance.template.title,
            "description": instance.template.description,
            "seed": instance.seed,
            "parameters": instance.chosen_parameters,
            "recent_changes": instance.recent_changes,
        }
