from __future__ import annotations

from dataclasses import dataclass
import unittest
from unittest.mock import patch

from tron.env import TronEnvironment
from tron.executor import CommandExecutor, CommandResult
from tron.models import BenchmarkConfig, ServiceProbe
from tron.observations import collect_observations
from tron.sampler import sample_scenario
from tron.scenario_catalog import load_catalog


@dataclass
class StubCommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str
    rejected: bool = False
    timed_out: bool = False
    action_cost: float = 0.0


class StubExecutor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 20.0) -> StubCommandResult:
        self.commands.append(command)
        mapping = {
            "kubectl -n tron get pods --no-headers": "nginx-123 1/1 Running\nredis-123 1/1 Running",
            "kubectl -n tron get services --no-headers": "nginx ClusterIP\nredis ClusterIP",
            "kubectl -n tron get deployments --no-headers": "nginx 1/1\nredis 1/1",
            "kubectl -n tron get endpoints --no-headers": "nginx 10.0.0.1:8080\nredis 10.0.0.2:6379",
            "CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./cleanup.sh": "",
            "CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./setup.sh": "",
            "kubectl apply --validate=false -f manifests/namespace.yaml": "",
            (
                "kubectl -n tron apply --validate=false "
                "-f manifests/configmap.yaml "
                "-f manifests/redis.yaml "
                "-f manifests/nginx.yaml "
                "-f manifests/ingress.yaml "
                "-f manifests/networkpolicy-base.yaml"
            ): "",
            "kubectl -n tron set env deployment/nginx REDIS_HOST-": "",
            "kubectl -n tron rollout status deployment/redis --timeout=120s": "",
            "kubectl -n tron rollout status deployment/nginx --timeout=120s": "",
        }
        return StubCommandResult(command=command, return_code=0, stdout=mapping.get(command, ""), stderr="")

    def run_argv(self, argv: list[str], timeout: float = 20.0) -> StubCommandResult:
        return StubCommandResult(command=" ".join(argv), return_code=0, stdout="", stderr="")

    def execute_action(self, command: str, timeout: float = 20.0) -> StubCommandResult:
        self.commands.append(command)
        if command.startswith("kubectl exec"):
            return StubCommandResult(command=command, return_code=0, stdout="ok", stderr="", action_cost=-0.02)
        if command == "kubectl -n tron get networkpolicy -o yaml":
            return StubCommandResult(
                command=command,
                return_code=0,
                stdout="apiVersion: v1\nitems:\n- kind: NetworkPolicy\n  metadata:\n    name: deny-nginx-egress",
                stderr="",
                action_cost=0.0,
            )
        if "REDIS_HOST" in command:
            return StubCommandResult(
                command=command,
                return_code=0,
                stdout="redis-stale",
                stderr="",
                action_cost=0.0,
            )
        return StubCommandResult(command=command, return_code=0, stdout="ok", stderr="", action_cost=0.0)

    def is_mutating(self, command: str) -> bool:
        return command.startswith("kubectl apply") or command.startswith("kubectl rollout restart")


class FailingRestoreExecutor(StubExecutor):
    def run(self, command: str, timeout: float = 20.0) -> StubCommandResult:
        if command.startswith("kubectl -n tron apply --validate=false ") and "manifests/nginx.yaml" in command:
            return StubCommandResult(
                command=command,
                return_code=1,
                stdout="",
                stderr="mock nginx apply failure",
            )
        return super().run(command, timeout=timeout)


class MissingClusterExecutor(StubExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.failed_restore = False

    def run(self, command: str, timeout: float = 20.0) -> StubCommandResult:
        if command == "kubectl apply --validate=false -f manifests/namespace.yaml" and not self.failed_restore:
            self.failed_restore = True
            self.commands.append(command)
            return StubCommandResult(
                command=command,
                return_code=1,
                stdout="",
                stderr=(
                    'E0407 23:22:58.734202 memcache.go:265] "Unhandled Error" '
                    'err="couldn\'t get current server API group list: '
                    'Get \\"https://0.0.0.0:59961/api?timeout=32s\\": '
                    'dial tcp 0.0.0.0:59961: connect: connection refused"'
                ),
            )
        return super().run(command, timeout=timeout)


class StubIncidentEngine:
    def __init__(self) -> None:
        self.injected: list[str] = []

    def inject(self, instance) -> list[str]:
        self.injected = instance.rendered_inject_commands
        return self.injected

    def verify_activation(self, instance):
        return []

    def verify_cluster_clues(self, instance):
        return []


class MissingClueIncidentEngine(StubIncidentEngine):
    def verify_cluster_clues(self, instance):
        from tron.models import CheckResult

        return [CheckResult(name="cluster-clue", ok=False, details="missing pod-side signal")]


class ExecutorTests(unittest.TestCase):
    def test_executor_rejects_non_kubectl_commands(self) -> None:
        executor = CommandExecutor(cwd=".")
        result = executor.execute_action("bash -lc 'echo nope'")
        self.assertTrue(result.rejected)
        self.assertIn("only kubectl and curl", result.stderr)

    def test_executor_rejects_kubectl_edit(self) -> None:
        executor = CommandExecutor(cwd=".")
        result = executor.execute_action("kubectl -n tron edit deployment nginx")
        self.assertTrue(result.rejected)
        self.assertIn("kubectl edit is not allowed", result.stderr)

    def test_executor_rejects_kubectl_scale(self) -> None:
        executor = CommandExecutor(cwd=".")
        result = executor.execute_action("kubectl -n tron scale deployment/nginx --replicas=0")
        self.assertTrue(result.rejected)
        self.assertIn("kubectl scale is not allowed", result.stderr)

    def test_executor_rejects_direct_replicaset_mutation(self) -> None:
        executor = CommandExecutor(cwd=".")
        result = executor.execute_action("kubectl -n tron patch rs nginx-123 -p '{\"spec\":{\"replicas\":0}}'")
        self.assertTrue(result.rejected)
        self.assertIn("ReplicaSet mutation", result.stderr)

    def test_executor_assigns_destructive_action_cost(self) -> None:
        executor = CommandExecutor(cwd=".")
        self.assertEqual(executor.action_cost("kubectl delete pod nginx"), -0.30)
        self.assertEqual(executor.action_cost("kubectl get pods -n tron"), 0.0)


class ObservationTests(unittest.TestCase):
    def test_default_observation_is_tiered(self) -> None:
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")
        observation = collect_observations(
            executor=StubExecutor(),
            config=BenchmarkConfig(),
            instance=instance,
            step_number=2,
            last_action="kubectl get pods -n tron",
            last_reward=0.0,
            service_probe=ServiceProbe(
                health_status="ok",
                data_status="error",
                http_status=503,
                latency_ms=240,
                score=0.7,
            ),
        )
        self.assertEqual(observation.step_number, 2)
        self.assertEqual(observation.service_probe.score, 0.7)
        self.assertIn("nginx-123", observation.cluster_summary.pods)
        self.assertTrue(observation.recent_change_hint)


class EnvironmentLoopTests(unittest.TestCase):
    @patch("tron.env.probe_service")
    def test_reset_uses_in_cluster_restore_by_default(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "error", 503, 250, 0.7)
        executor = StubExecutor()
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=executor,
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17)

        self.assertIn("kubectl apply --validate=false -f manifests/namespace.yaml", executor.commands)
        self.assertIn("kubectl -n tron set env deployment/nginx REDIS_HOST-", executor.commands)
        self.assertNotIn("CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./cleanup.sh", executor.commands)
        self.assertNotIn("CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./setup.sh", executor.commands)

    @patch("tron.env.probe_service")
    def test_reset_hard_reset_recreates_cluster(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "error", 503, 250, 0.7)
        executor = StubExecutor()
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=executor,
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17, hard_reset=True)

        self.assertIn("CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./cleanup.sh", executor.commands)
        self.assertIn("CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./setup.sh", executor.commands)

    @patch("tron.env.probe_service")
    def test_reset_reports_exact_restore_command_on_failure(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "error", 503, 250, 0.7)
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=FailingRestoreExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17)

        self.assertIn("baseline restore failed", str(ctx.exception))
        self.assertIn("manifests/nginx.yaml", str(ctx.exception))
        self.assertIn("mock nginx apply failure", str(ctx.exception))

    @patch("tron.env.probe_service")
    def test_reset_falls_back_to_hard_reset_when_cluster_is_unreachable(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "error", 503, 250, 0.7)
        executor = MissingClusterExecutor()
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=executor,
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17)

        self.assertIn("kubectl apply --validate=false -f manifests/namespace.yaml", executor.commands)
        self.assertIn(
            "CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./cleanup.sh",
            executor.commands,
        )
        self.assertIn(
            "CLUSTER_NAME=tron-lab INGRESS_HOST=tron.localhost INGRESS_PORT=8080 NAMESPACE=tron bash ./setup.sh",
            executor.commands,
        )

    @patch("tron.env.probe_service")
    def test_reset_raises_when_resource_incident_is_not_externally_visible(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "ok", 200, 90, 1.0)
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0, trusted_timeout_seconds=0.01),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            env.reset(scenario_id="cpu-limits-too-low", seed=17)

        self.assertIn("scenario did not become externally visible", str(ctx.exception))

    @patch("tron.env.probe_service")
    @patch("tron.env.time.sleep", return_value=None)
    def test_reset_waits_past_transient_health_failure_for_steady_state(self, _sleep_mock, probe_service_mock) -> None:
        probe_service_mock.side_effect = [
            ServiceProbe("error", "error", 503, 250, 0.4),
            ServiceProbe("ok", "error", 503, 250, 0.7),
        ]
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.1),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        observation = env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17)

        self.assertEqual(observation.service_probe.health_status, "ok")
        self.assertEqual(observation.service_probe.data_status, "error")

    @patch("tron.env.probe_service")
    def test_reset_allows_non_degrading_probe_scenario(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "ok", 200, 90, 1.0)
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        observation = env.reset(scenario_id="readiness-probe-too-permissive", seed=17)

        self.assertEqual(observation.service_probe.score, 1.0)

    @patch("tron.env.probe_service")
    def test_reset_allows_unreachable_scaled_to_zero_incident(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("unreachable", "unreachable", None, None, 0.0)
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )

        observation = env.reset(scenario_id="deployment-scaled-to-zero", seed=17)

        self.assertEqual(observation.service_probe.score, 0.0)

    @patch("tron.env.probe_service")
    def test_reset_raises_when_cluster_side_clue_is_missing(self, probe_service_mock) -> None:
        probe_service_mock.return_value = ServiceProbe("ok", "error", 503, 250, 0.7)
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=MissingClueIncidentEngine(),
        )

        with self.assertRaises(RuntimeError) as ctx:
            env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17)

        self.assertIn("scenario cluster clues missing", str(ctx.exception))

    @patch("tron.env.probe_service")
    @patch("tron.env.time.sleep", return_value=None)
    def test_rollout_status_waits_briefly_for_blackbox_recovery(self, _sleep_mock, probe_service_mock) -> None:
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")
        probe_service_mock.side_effect = [
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "ok", 200, 90, 1.0),
        ]
        executor = StubExecutor()
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.1),
            executor=executor,
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )
        env.current_instance = instance
        env.current_observation = collect_observations(
            executor=executor,
            config=env.config,
            instance=instance,
            step_number=0,
            last_action=None,
            last_reward=0.0,
            service_probe=ServiceProbe("ok", "error", 503, 250, 0.7),
        )
        env.current_service_score = env.current_observation.service_probe.score

        transition = env.step("kubectl -n tron rollout status deployment/nginx --timeout=120s")

        self.assertTrue(transition.done)
        self.assertEqual(transition.service_score, 1.0)
        self.assertEqual(transition.observation.service_probe.data_status, "ok")

    @patch("tron.env.probe_service")
    def test_step_updates_reward_and_completion(self, probe_service_mock) -> None:
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")
        probe_service_mock.side_effect = [
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "ok", 200, 90, 1.0),
        ]
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )
        env.current_instance = instance
        env.current_observation = env.observe(instance)
        env.current_service_score = env.current_observation.service_probe.score

        transition = env.step("kubectl get pods -n tron")

        self.assertEqual(transition.reward, 0.3)
        self.assertTrue(transition.done)
        self.assertEqual(env.step_number, 1)
        self.assertEqual(env.steps[0].command, "kubectl get pods -n tron")

    @patch("tron.env.probe_service")
    def test_discriminating_read_bonus_applies_for_networkpolicy_read(self, probe_service_mock) -> None:
        instance = sample_scenario(load_catalog(), seed=13, scenario_id="networkpolicy-blocks-nginx-to-redis")
        probe_service_mock.side_effect = [
            ServiceProbe("ok", "timeout", 200, 3000, 0.7),
            ServiceProbe("ok", "timeout", 200, 3000, 0.7),
        ]
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )
        env.current_instance = instance
        env.current_observation = env.observe(instance)
        env.current_service_score = env.current_observation.service_probe.score

        transition = env.step("kubectl -n tron get networkpolicy -o yaml")

        self.assertEqual(transition.reward, 0.02)

    @patch("tron.env.probe_service")
    def test_repeated_no_effect_restart_gets_penalty(self, probe_service_mock) -> None:
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")
        probe_service_mock.side_effect = [
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "error", 503, 250, 0.7),
        ]
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )
        env.current_instance = instance
        env.current_observation = env.observe(instance)
        env.current_service_score = env.current_observation.service_probe.score

        first = env.step("kubectl -n tron rollout restart deployment/nginx")
        second = env.step("kubectl -n tron rollout restart deployment/nginx")

        self.assertEqual(first.reward, 0.0)
        self.assertEqual(second.reward, -0.05)

    @patch("tron.env.probe_service")
    def test_third_no_effect_restart_gets_stronger_penalty(self, probe_service_mock) -> None:
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")
        probe_service_mock.side_effect = [
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "error", 503, 250, 0.7),
        ]
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=5, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )
        env.current_instance = instance
        env.current_observation = env.observe(instance)
        env.current_service_score = env.current_observation.service_probe.score

        env.step("kubectl -n tron rollout restart deployment/nginx")
        second = env.step("kubectl -n tron rollout restart deployment/nginx")
        third = env.step("kubectl -n tron rollout restart deployment/nginx")

        self.assertEqual(second.reward, -0.05)
        self.assertEqual(third.reward, -0.1)

    @patch("tron.env.probe_service")
    def test_live_runtime_read_gets_bonus(self, probe_service_mock) -> None:
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")
        probe_service_mock.side_effect = [
            ServiceProbe("ok", "error", 503, 250, 0.7),
            ServiceProbe("ok", "error", 503, 250, 0.7),
        ]
        env = TronEnvironment(
            BenchmarkConfig(max_agent_steps=4, mutation_settle_seconds=0.0),
            executor=StubExecutor(),
            catalog=load_catalog(),
            incident_engine=StubIncidentEngine(),
        )
        env.current_instance = instance
        env.current_observation = env.observe(instance)
        env.current_service_score = env.current_observation.service_probe.score

        transition = env.step(
            "kubectl -n tron get pods -l app=nginx -o jsonpath='{.items[*].spec.containers[*].env[?(@.name==\"REDIS_HOST\")].value}'"
        )

        self.assertEqual(transition.reward, 0.03)


if __name__ == "__main__":
    unittest.main()
