from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from baseline import llm_agent, naive
from eval import run_eval, summarize_results
from models import ClusterSummary, ObservationBundle, ServiceProbe
from sampler import sample_scenario
from scenario_catalog import load_catalog


def make_observation() -> ObservationBundle:
    return ObservationBundle(
        incident_brief="data path is degraded",
        step_number=1,
        last_action="kubectl -n tron get pods",
        last_reward=0.0,
        service_probe=ServiceProbe(
            health_status="ok",
            data_status="error",
            http_status=503,
            latency_ms=240,
            score=0.7,
        ),
        cluster_summary=ClusterSummary(
            pods="nginx Running; redis Running",
            services="nginx ClusterIP; redis ClusterIP",
            deployments="nginx 1/1; redis 1/1",
            endpoints="nginx 10.0.0.1:8080; redis 10.0.0.2:6379",
        ),
        recent_change_hint="Recent change: app-config REDIS_HOST was updated.",
    )


class NaiveAgentTests(unittest.TestCase):
    def test_naive_agent_cycles_through_costly_playbook(self) -> None:
        agent = naive.build_agent()
        instance = sample_scenario(load_catalog(), seed=11, scenario_id="bad-rollout-wrong-redis-host")
        observation = make_observation()

        actions = [agent.next_action(instance, observation, []) for _ in range(4)]

        self.assertEqual(actions[0], "kubectl -n tron get pods")
        self.assertIn("rollout restart", actions[1])
        self.assertIn("rollout restart", actions[2])
        self.assertIn("kubectl apply", actions[3])


class LLMAgentTests(unittest.TestCase):
    def test_build_prompt_contains_tiered_observation_json(self) -> None:
        instance = sample_scenario(load_catalog(), seed=13, scenario_id="networkpolicy-blocks-nginx-to-redis")
        prompt = llm_agent.build_prompt(instance, make_observation(), history=[{"command": "kubectl -n tron get pods"}])

        self.assertIn('"scenario"', prompt)
        self.assertIn('"service_probe"', prompt)
        self.assertIn('"recent_history"', prompt)

    def test_parse_command_rejects_multi_line_output(self) -> None:
        with self.assertRaises(ValueError):
            llm_agent.parse_command("kubectl get pods\nkubectl get svc")

    def test_static_plan_client_returns_one_command(self) -> None:
        agent = llm_agent.build_agent(client=llm_agent.StaticPlanClient(commands=["kubectl -n tron get pods"]))
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")

        command = agent.next_action(instance, make_observation(), history=[])

        self.assertEqual(command, "kubectl -n tron get pods")


class SummaryTests(unittest.TestCase):
    def test_summary_metrics_capture_recovery_and_repetition(self) -> None:
        rows = [
            {
                "agent": "naive",
                "initial_service_score": 0.4,
                "final_service_score": 1.0,
                "total_reward": 0.3,
                "steps": [
                    {"index": 1, "command": "kubectl -n tron get pods", "service_score": 0.4, "action_class": "diagnostic"},
                    {"index": 2, "command": "kubectl -n tron get pods", "service_score": 0.4, "action_class": "diagnostic"},
                    {"index": 3, "command": "kubectl -n tron rollout restart deployment/nginx", "service_score": 1.0, "action_class": "destructive"},
                ],
                "oracle": {"score": 1.0, "verdict": "success"},
            }
        ]

        summary = summarize_results.build_summary(rows)

        self.assertEqual(summary["overall"]["avg_steps_to_first_improvement"], 3.0)
        self.assertEqual(summary["overall"]["avg_steps_to_full_recovery"], 3.0)
        self.assertEqual(summary["overall"]["repeated_ineffective_actions"], 1)
        self.assertEqual(summary["overall"]["full_recovery_rate"], 1.0)

    def test_seed_plan_loader_supports_structured_scenarios(self) -> None:
        payload = "scenarios:\n  - id: bad-rollout-wrong-redis-host\n    seeds: [11, 21]\n"
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml") as handle:
            handle.write(payload)
            handle.flush()
            plan = run_eval.load_seed_plan(Path(handle.name))

        self.assertEqual(plan[0]["id"], "bad-rollout-wrong-redis-host")
        self.assertEqual(plan[0]["seeds"], [11, 21])


if __name__ == "__main__":
    unittest.main()
