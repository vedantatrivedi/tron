from __future__ import annotations

import unittest

from tron.action_analysis import classify_action, command_family
from tron.models import AgentStep, ClusterConfig
from tron.rewards import discriminating_read_bonus, repeated_no_effect_penalty
from tron.runtime_setup import (
    build_baseline_restore_commands,
    build_cluster_env_prefix,
    build_hard_reset_commands,
    run_checked_commands,
)


class ActionAnalysisTests(unittest.TestCase):
    def test_classify_action_marks_rollout_status_as_diagnostic(self) -> None:
        self.assertEqual(
            classify_action("kubectl -n tron rollout status deployment/nginx"),
            "diagnostic",
        )

    def test_command_family_groups_restart_commands(self) -> None:
        self.assertEqual(
            command_family("kubectl -n tron rollout restart deployment/nginx"),
            "restart_nginx",
        )


class RewardPolicyTests(unittest.TestCase):
    def test_discriminating_read_bonus_requires_matching_signal(self) -> None:
        self.assertEqual(
            discriminating_read_bonus(
                "kubectl -n tron get configmap app-config -o yaml",
                0,
                "data:\n  REDIS_HOST: redis-shadow\n",
            ),
            0.02,
        )
        self.assertEqual(
            discriminating_read_bonus(
                "kubectl -n tron get configmap app-config -o yaml",
                0,
                "metadata:\n  name: app-config\n",
            ),
            0.0,
        )

    def test_repeated_no_effect_penalty_scales_for_restarts(self) -> None:
        prior_steps = [
            AgentStep(
                command="kubectl -n tron rollout restart deployment/nginx",
                return_code=0,
                stdout="",
                stderr="",
                reward=0.0,
            ),
            AgentStep(
                command="kubectl -n tron rollout restart deployment/nginx",
                return_code=0,
                stdout="",
                stderr="",
                reward=-0.05,
            ),
        ]

        self.assertEqual(
            repeated_no_effect_penalty(
                "kubectl -n tron rollout restart deployment/nginx",
                new_service_score=0.7,
                previous_service_score=0.7,
                previous_steps=prior_steps,
            ),
            -0.1,
        )


class RuntimeSetupTests(unittest.TestCase):
    def test_build_cluster_env_prefix_uses_cluster_fields(self) -> None:
        prefix = build_cluster_env_prefix(
            ClusterConfig(
                cluster_name="demo",
                namespace="tron",
                ingress_host="tron.localhost",
                ingress_port=18080,
            )
        )

        self.assertIn("CLUSTER_NAME=demo", prefix)
        self.assertIn("INGRESS_PORT=18080", prefix)

    def test_build_baseline_restore_commands_clears_runtime_override(self) -> None:
        commands = build_baseline_restore_commands("tron")

        self.assertIn("kubectl -n tron set env deployment/nginx REDIS_HOST-", commands)
        self.assertEqual(commands[0], "kubectl apply --validate=false -f manifests/namespace.yaml")
        self.assertIn("manifests/nginx.yaml", commands[1])
        self.assertEqual(commands[-1], "kubectl -n tron rollout status deployment/nginx --timeout=120s")

    def test_build_baseline_restore_commands_uses_configured_rollout_timeout(self) -> None:
        commands = build_baseline_restore_commands("tron", rollout_timeout_seconds=30)

        self.assertIn("kubectl -n tron rollout status deployment/redis --timeout=30s", commands)
        self.assertIn("kubectl -n tron rollout status deployment/nginx --timeout=30s", commands)

    def test_build_hard_reset_commands_wraps_cleanup_and_setup(self) -> None:
        commands = build_hard_reset_commands(
            ClusterConfig(cluster_name="demo", namespace="tron", ingress_host="tron.localhost", ingress_port=8080)
        )

        self.assertEqual(len(commands), 2)
        self.assertIn("bash ./cleanup.sh", commands[0])
        self.assertIn("bash ./setup.sh", commands[1])

    def test_run_checked_commands_raises_with_stage_context(self) -> None:
        class StubExecutor:
            def __init__(self) -> None:
                self.calls = []

            def run(self, command: str, timeout: float = 20.0):
                self.calls.append((command, timeout))
                return type("Result", (), {"return_code": 1, "stderr": "boom", "stdout": ""})()

        with self.assertRaises(RuntimeError) as ctx:
            run_checked_commands(StubExecutor(), ["kubectl get pods"], timeout=10.0, stage="baseline restore")

        self.assertIn("baseline restore failed", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
