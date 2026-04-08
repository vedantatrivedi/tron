from __future__ import annotations

"""Incident injection, verification, and restoration helpers."""

from typing import Protocol

from tron.checks import evaluate_check, format_failed_checks
from tron.models import CheckResult, ScenarioInstance, ScenarioTemplate
from tron.sampler import get_scenario, sample_scenario


class SupportsCommandExecution(Protocol):
    def run(self, command: str, timeout: float = 20.0): ...
    def run_argv(self, argv: list[str], timeout: float = 20.0): ...


class IncidentEngine:
    """Apply a scenario, verify it activated, and later restore baseline state."""

    def __init__(self, executor: SupportsCommandExecution) -> None:
        self.executor = executor

    def inject(self, instance: ScenarioInstance) -> list[str]:
        applied: list[str] = []
        for command in [*instance.rendered_inject_commands, *instance.rendered_distractor_commands]:
            result = self.executor.run(command)
            if result.return_code != 0:
                raise RuntimeError(
                    f"incident injection failed for `{command}`: {result.stderr or result.stdout}"
                )
            applied.append(command)
        return applied

    def verify_activation(self, instance: ScenarioInstance) -> list[CheckResult]:
        return [evaluate_check(self.executor, check) for check in instance.template.activation_checks]

    def verify_cluster_clues(
        self,
        instance: ScenarioInstance,
        clue_checks: list | None = None,
    ) -> list[CheckResult]:
        clue_checks = clue_checks or instance.template.cluster_clue_checks or instance.template.activation_checks[:1]
        return [evaluate_check(self.executor, check) for check in clue_checks]

    def restore(self, instance: ScenarioInstance) -> list[str]:
        restored: list[str] = []
        for command in [
            *instance.rendered_restore_commands,
            *instance.rendered_distractor_restore_commands,
        ]:
            result = self.executor.run(command)
            if result.return_code != 0:
                raise RuntimeError(
                    f"incident restore failed for `{command}`: {result.stderr or result.stdout}"
                )
            restored.append(command)
        return restored

    def inject_by_id(
        self,
        catalog: list[ScenarioTemplate],
        scenario_id: str,
        seed: int,
    ) -> ScenarioInstance:
        instance = sample_scenario(catalog, seed=seed, scenario_id=get_scenario(catalog, scenario_id).id)
        self.inject(instance)
        failure = format_failed_checks(
            f"activation checks failed for {scenario_id}: ",
            self.verify_activation(instance),
        )
        if failure:
            raise RuntimeError(failure)
        return instance
