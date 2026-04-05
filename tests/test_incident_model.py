from __future__ import annotations

from dataclasses import dataclass
import unittest

from tron.incident_engine import IncidentEngine
from tron.scenario_catalog import load_catalog
from tron.sampler import get_scenario, sample_scenario


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
                "bridge-crashloop-bad-command",
                "deployment-scaled-to-zero",
                "wrong-redis-host-plus-cpu-throttle",
                "networkpolicy-plus-secondary-drift",
            },
        )

    def test_catalog_ids_are_unique(self) -> None:
        catalog = load_catalog()
        scenario_ids = [scenario.id for scenario in catalog]

        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))

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
            if scenario.requires_service_degradation:
                self.assertTrue(scenario.cluster_clue_checks, scenario.id)

    def test_sampling_is_seeded_and_reproducible(self) -> None:
        catalog = load_catalog()
        first = sample_scenario(catalog, seed=17, scenario_id="bad-rollout-wrong-redis-host")
        second = sample_scenario(catalog, seed=17, scenario_id="bad-rollout-wrong-redis-host")
        self.assertEqual(first.chosen_parameters, second.chosen_parameters)
        self.assertEqual(first.rendered_inject_commands, second.rendered_inject_commands)
        self.assertIn("incident=bad-rollout-wrong-redis-host", first.recent_changes[0])

    def test_sampling_renders_cluster_clue_checks(self) -> None:
        instance = sample_scenario(load_catalog(), seed=11, scenario_id="bad-rollout-wrong-redis-host")

        self.assertTrue(instance.template.cluster_clue_checks)
        self.assertNotIn("{bad_host}", instance.template.cluster_clue_checks[0].success_substring)
        self.assertEqual(
            instance.template.cluster_clue_checks[0].success_substring,
            instance.chosen_parameters["bad_host"],
        )

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

        self.assertEqual(len(executor.shell_commands), 9)
        self.assertEqual(len(checks), 2)
        self.assertTrue(all(check.ok for check in checks))

    def test_hard_scenario_renders_distractor_mutation(self) -> None:
        instance = sample_scenario(
            load_catalog(),
            seed=5,
            scenario_id="networkpolicy-plus-secondary-drift",
        )

        self.assertTrue(instance.rendered_distractor_commands)
        self.assertIn("annotate ingress tron-ingress", instance.rendered_distractor_commands[0])
        self.assertIn("Unrelated change: ingress metadata was updated", instance.recent_changes[-1])

    def test_readiness_probe_scenario_uses_json_patch_to_replace_handler(self) -> None:
        template = get_scenario(load_catalog(), "readiness-probe-too-permissive")
        command = template.inject_commands[0]

        self.assertIn("patch deployment nginx --type=json", command)
        self.assertIn("\"op\":\"remove\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/httpGet\"", command)
        self.assertIn("\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/exec\"", command)
        self.assertFalse(template.requires_service_degradation)

    def test_resource_scenarios_enable_runtime_pressure_and_restore_config(self) -> None:
        cpu = get_scenario(load_catalog(), "cpu-limits-too-low")
        memory = get_scenario(load_catalog(), "memory-limits-too-low")

        self.assertIn("BRIDGE_CPU_BURN_MS", cpu.inject_commands[0])
        self.assertIn("rollout restart deployment/nginx", cpu.inject_commands[0])
        self.assertIn("kubectl apply -f manifests/configmap.yaml", cpu.restore_commands)
        self.assertIn("cpu-burn-profile-applied", {check.name for check in cpu.cluster_clue_checks})

        self.assertIn("BRIDGE_MEMORY_BURST_MB", memory.inject_commands[0])
        self.assertIn("rollout restart deployment/nginx", memory.inject_commands[0])
        self.assertIn("kubectl apply -f manifests/configmap.yaml", memory.restore_commands)
        self.assertIn("bridge-restarts-after-memory-pressure", {check.name for check in memory.cluster_clue_checks})

    def test_new_deployment_incidents_have_reviewable_repairs(self) -> None:
        crashloop = get_scenario(load_catalog(), "bridge-crashloop-bad-command")
        scaled = get_scenario(load_catalog(), "deployment-scaled-to-zero")

        self.assertIn("containers/1/command/1", crashloop.inject_commands[0])
        self.assertIn("/app/bridge.py", crashloop.repair_checks[0].success_substring)

        self.assertIn("\"replicas\":0", scaled.inject_commands[0])
        self.assertEqual("1", scaled.repair_checks[0].success_substring)

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

    def test_degrading_scenarios_publish_explicit_cluster_clues(self) -> None:
        scenarios = {
            scenario.id: scenario
            for scenario in load_catalog()
            if scenario.requires_service_degradation
        }

        self.assertIn("configmap-has-bad-host", {check.name for check in scenarios["bad-rollout-wrong-redis-host"].cluster_clue_checks})
        self.assertIn("redis-endpoints-empty", {check.name for check in scenarios["service-selector-mismatch"].cluster_clue_checks})
        self.assertIn(
            "bridge-restarts-after-memory-pressure",
            {check.name for check in scenarios["memory-limits-too-low"].cluster_clue_checks},
        )


if __name__ == "__main__":
    unittest.main()
