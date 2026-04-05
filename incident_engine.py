from __future__ import annotations

"""Incident injection, verification, and restoration helpers."""

from typing import Protocol

from models import CheckResult, ScenarioInstance, ScenarioTemplate
from sampler import get_scenario, sample_scenario


class SupportsCommandExecution(Protocol):
    def run(self, command: str, timeout: float = 20.0): ...
    def run_argv(self, argv: list[str], timeout: float = 20.0): ...


def _evaluate_check(executor: SupportsCommandExecution, check) -> CheckResult:
    result = executor.run_argv(check.command)
    stdout = result.stdout
    details = stdout or result.stderr
    if check.match_mode == "equals":
        ok = result.return_code == 0 and stdout == check.success_substring
    elif check.success_substring:
        ok = result.return_code == 0 and check.success_substring in stdout
    else:
        ok = result.return_code == 0 and stdout == ""
    return CheckResult(name=check.name, ok=ok, details=details)


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
        return [_evaluate_check(self.executor, check) for check in instance.template.activation_checks]

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
        failed = [check for check in self.verify_activation(instance) if not check.ok]
        if failed:
            raise RuntimeError(
                f"activation checks failed for {scenario_id}: "
                + ", ".join(f"{check.name} -> {check.details}" for check in failed)
            )
        return instance
