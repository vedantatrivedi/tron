from __future__ import annotations

from dataclasses import dataclass
import unittest
from unittest.mock import patch

from env import TronEnvironment
from executor import CommandExecutor, CommandResult
from models import BenchmarkConfig, ServiceProbe
from observations import collect_observations
from sampler import sample_scenario
from scenario_catalog import load_catalog


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
            "./cleanup.sh": "",
            "./setup.sh": "",
        }
        return StubCommandResult(command=command, return_code=0, stdout=mapping.get(command, ""), stderr="")

    def run_argv(self, argv: list[str], timeout: float = 20.0) -> StubCommandResult:
        return StubCommandResult(command=" ".join(argv), return_code=0, stdout="", stderr="")

    def execute_action(self, command: str, timeout: float = 20.0) -> StubCommandResult:
        self.commands.append(command)
        if command.startswith("kubectl exec"):
            return StubCommandResult(command=command, return_code=0, stdout="ok", stderr="", action_cost=-0.02)
        return StubCommandResult(command=command, return_code=0, stdout="ok", stderr="", action_cost=0.0)

    def is_mutating(self, command: str) -> bool:
        return command.startswith("kubectl apply") or command.startswith("kubectl rollout restart")


class StubIncidentEngine:
    def __init__(self) -> None:
        self.injected: list[str] = []

    def inject(self, instance) -> list[str]:
        self.injected = instance.rendered_inject_commands
        return self.injected

    def verify_activation(self, instance):
        return []


class ExecutorTests(unittest.TestCase):
    def test_executor_rejects_non_kubectl_commands(self) -> None:
        executor = CommandExecutor(cwd=".")
        result = executor.execute_action("bash -lc 'echo nope'")
        self.assertTrue(result.rejected)
        self.assertIn("only kubectl and curl", result.stderr)

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
    @patch("env.probe_service")
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


if __name__ == "__main__":
    unittest.main()
