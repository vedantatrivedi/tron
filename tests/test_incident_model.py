from __future__ import annotations

from dataclasses import dataclass
import unittest

from incident_engine import IncidentEngine
from scenario_catalog import load_catalog
from sampler import get_scenario, sample_scenario


@dataclass
class CommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str


class FakeExecutor:
    def __init__(self) -> None:
        self.shell_commands: list[str] = []
        self.argv_commands: list[list[str]] = []

    def run(self, command: str, timeout: float = 20.0) -> CommandResult:
        self.shell_commands.append(command)
        return CommandResult(command=command, return_code=0, stdout="", stderr="")

    def run_argv(self, argv: list[str], timeout: float = 20.0) -> CommandResult:
        self.argv_commands.append(argv)
        script = " ".join(argv)
        stdout = ""
        if "jsonpath={.data.REDIS_HOST}" in script:
            stdout = "redis-shadow"
        elif "printenv REDIS_HOST" in script:
            stdout = "redis-shadow"
        elif "networkpolicy" in script and "-o name" in script:
            stdout = "networkpolicy.networking.k8s.io/block-redis-egress"
        elif ".spec.selector.app" in script:
            stdout = "redis-shadow"
        return CommandResult(command=" ".join(argv), return_code=0, stdout=stdout, stderr="")


class IncidentModelTests(unittest.TestCase):
    def test_catalog_contains_required_incidents(self) -> None:
        scenario_ids = {scenario.id for scenario in load_catalog()}
        self.assertEqual(
            scenario_ids,
            {
                "bad-rollout-wrong-redis-host",
                "configmap-fixed-but-pods-stale",
                "service-selector-mismatch",
                "cpu-limits-too-low",
                "memory-limits-too-low",
                "readiness-probe-too-permissive",
                "networkpolicy-blocks-nginx-to-redis",
                "ingress-path-rewrite-bug",
                "wrong-redis-host-plus-cpu-throttle",
                "networkpolicy-plus-secondary-drift",
            },
        )

    def test_every_scenario_has_required_review_fields(self) -> None:
        for scenario in load_catalog():
            self.assertTrue(scenario.title)
            self.assertTrue(scenario.trigger_context)
            self.assertTrue(scenario.user_visible_symptom)
            self.assertTrue(scenario.hidden_faults)
            self.assertTrue(scenario.distractors)
            self.assertTrue(scenario.difficulty)
            self.assertTrue(scenario.parameters)
            self.assertTrue(scenario.inject_commands)
            self.assertTrue(scenario.activation_checks)
            self.assertTrue(scenario.restore_commands)

    def test_sampling_is_seeded_and_reproducible(self) -> None:
        catalog = load_catalog()
        first = sample_scenario(catalog, seed=17, scenario_id="bad-rollout-wrong-redis-host")
        second = sample_scenario(catalog, seed=17, scenario_id="bad-rollout-wrong-redis-host")
        self.assertEqual(first.chosen_parameters, second.chosen_parameters)
        self.assertEqual(first.rendered_inject_commands, second.rendered_inject_commands)
        self.assertIn("incident=bad-rollout-wrong-redis-host", first.recent_changes[0])

    def test_sampling_without_id_still_returns_known_template(self) -> None:
        catalog = load_catalog()
        instance = sample_scenario(catalog, seed=3)
        self.assertIn(instance.template.id, {scenario.id for scenario in catalog})

    def test_get_scenario_returns_selected_template(self) -> None:
        template = get_scenario(load_catalog(), "ingress-path-rewrite-bug")
        self.assertEqual(template.id, "ingress-path-rewrite-bug")
        self.assertEqual(template.scenario_id, "ingress-path-rewrite-bug")

    def test_incident_engine_injects_verifies_and_restores(self) -> None:
        executor = FakeExecutor()
        engine = IncidentEngine(executor)
        instance = sample_scenario(
            load_catalog(),
            seed=5,
            scenario_id="networkpolicy-plus-secondary-drift",
        )
        engine.inject(instance)
        checks = engine.verify_activation(instance)
        engine.restore(instance)

        self.assertEqual(len(executor.shell_commands), 7)
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(check.ok for check in checks))

    def test_inject_by_id_runs_activation_checks(self) -> None:
        executor = FakeExecutor()
        engine = IncidentEngine(executor)
        instance = engine.inject_by_id(
            load_catalog(),
            scenario_id="networkpolicy-blocks-nginx-to-redis",
            seed=7,
        )
        self.assertEqual(instance.template.id, "networkpolicy-blocks-nginx-to-redis")
        self.assertTrue(executor.shell_commands)
        self.assertTrue(executor.argv_commands)


if __name__ == "__main__":
    unittest.main()
