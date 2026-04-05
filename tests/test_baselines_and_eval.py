from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tron.action_analysis import command_family
from baseline import llm_agent, naive
from eval import run_eval, summarize_results
from tron.models import BenchmarkConfig, ClusterSummary, ObservationBundle, ServiceProbe
from tron.sampler import sample_scenario
from tron.scenario_catalog import load_catalog


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
    def test_command_family_is_shared_for_runtime_env_reads(self) -> None:
        family = command_family(
            "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST"
        )

        self.assertEqual(family, "live_runtime_env")

    def test_build_prompt_contains_tiered_observation_json(self) -> None:
        instance = sample_scenario(load_catalog(), seed=13, scenario_id="networkpolicy-blocks-nginx-to-redis")
        prompt = llm_agent.build_prompt(instance, make_observation(), history=[{"command": "kubectl -n tron get pods"}])

        self.assertIn('"scenario"', prompt)
        self.assertIn('"service_probe"', prompt)
        self.assertIn('"recent_history"', prompt)
        self.assertIn('"working_memory"', prompt)
        self.assertIn('"target_namespace": "tron"', prompt)
        self.assertIn("Stay in namespace tron", prompt)
        self.assertIn("Do not tailor to a single canned incident", prompt)
        self.assertIn("Aim for a real fix, not just a temporary workaround", prompt)
        self.assertIn("rather than shell pipelines or jq", prompt)
        self.assertIn("mounted config blobs and embedded scripts as lower priority", prompt)
        self.assertIn("visible config already looks healthy", prompt)
        self.assertIn("do not restart it again until you have one new fact", prompt)
        self.assertIn("Prefer robust pod-level reads over clever one-liners", prompt)
        self.assertIn("Avoid complex jsonpath filters over all pods", prompt)
        self.assertIn('Return exactly one single-line JSON object with keys "intent" and "command"', prompt)

    def test_parse_command_rejects_multi_line_output(self) -> None:
        with self.assertRaises(ValueError):
            llm_agent.parse_command("kubectl get pods\nkubectl get svc")

    def test_parse_response_supports_structured_json(self) -> None:
        proposal = llm_agent.parse_response(
            '{"intent":"check live env","command":"kubectl -n tron get pods"}'
        )

        self.assertEqual(proposal.intent, "check live env")
        self.assertEqual(proposal.command, "kubectl -n tron get pods")

    def test_static_plan_client_returns_one_command(self) -> None:
        agent = llm_agent.build_agent(client=llm_agent.StaticPlanClient(commands=["kubectl -n tron get pods"]))
        instance = sample_scenario(load_catalog(), seed=17, scenario_id="bad-rollout-wrong-redis-host")

        command = agent.next_action(instance, make_observation(), history=[])

        self.assertEqual(command, "kubectl -n tron get pods")
        self.assertEqual(agent.last_intent, "run the next fallback benchmark step")

    def test_describe_action_reports_local_intent(self) -> None:
        agent = llm_agent.build_agent(client=llm_agent.StaticPlanClient(commands=["kubectl -n tron get networkpolicy -o yaml"]))
        instance = sample_scenario(load_catalog(), seed=13, scenario_id="networkpolicy-blocks-nginx-to-redis")

        agent.next_action(instance, make_observation(), history=[])
        description = agent.describe_action(
            "kubectl -n tron get networkpolicy -o yaml",
            instance,
            make_observation(),
            history=[],
        )

        self.assertEqual("run the next fallback benchmark step", description)

    def test_live_runtime_fact_is_extracted_from_history(self) -> None:
        facts = llm_agent.summarize_observed_facts(
            [
                {
                    "command": "kubectl -n tron get pods -l app=nginx -o jsonpath='{.items[*].spec.containers[*].env[?(@.name==\"REDIS_HOST\")].value}'",
                    "stdout": "redis-stale",
                }
            ]
        )

        self.assertIn("live runtime value observed: redis-stale", facts)

    def test_openai_client_defaults_to_cheapest_configured_model(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            client = llm_agent.build_client_from_env()

        self.assertIsInstance(client, llm_agent.OpenAIResponsesClient)
        self.assertEqual(client.model, "gpt-5-mini")

    def test_openai_gpt5_models_use_responses_api_client(self) -> None:
        with patch.dict(
            "os.environ",
            {"OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-5-mini"},
            clear=True,
        ):
            client = llm_agent.build_client_from_env()

        self.assertIsInstance(client, llm_agent.OpenAIResponsesClient)
        self.assertEqual(client.model, "gpt-5-mini")

    def test_anthropic_client_defaults_to_cheapest_configured_model(self) -> None:
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            client = llm_agent.build_client_from_env()

        self.assertIsInstance(client, llm_agent.AnthropicMessagesClient)
        self.assertEqual(client.model, "claude-3-haiku-20240307")


class SummaryTests(unittest.TestCase):
    def test_summary_metrics_capture_recovery_and_repetition(self) -> None:
        rows = [
            {
                "agent": "naive",
                "scenario_id": "bad-rollout-wrong-redis-host",
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

    def test_machine_report_contains_per_scenario_status(self) -> None:
        rows = [
            {
                "agent": "llm",
                "scenario_id": "service-selector-mismatch",
                "step_count": 3,
                "initial_service_score": 0.7,
                "final_service_score": 1.0,
                "total_reward": 0.2,
                "steps": [],
                "oracle": {"score": 1.0, "verdict": "success"},
            }
        ]

        report = summarize_results.build_machine_report(rows)

        self.assertIn("service-selector-mismatch", report["by_scenario"])
        self.assertEqual(report["by_scenario"]["service-selector-mismatch"]["success_rate"], 1.0)

    def test_seed_plan_loader_supports_structured_scenarios(self) -> None:
        payload = "scenarios:\n  - id: bad-rollout-wrong-redis-host\n    seeds: [11, 21]\n"
        with tempfile.NamedTemporaryFile("w+", suffix=".yaml") as handle:
            handle.write(payload)
            handle.flush()
            plan = run_eval.load_seed_plan(Path(handle.name))

        self.assertEqual(plan[0]["id"], "bad-rollout-wrong-redis-host")
        self.assertEqual(plan[0]["seeds"], [11, 21])

    def test_explicit_scenario_outside_seed_plan_is_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            seeds_path = Path(temp_dir) / "seeds.yaml"
            seeds_path.write_text(
                "scenarios:\n  - id: bad-rollout-wrong-redis-host\n    seeds: [11]\n",
                encoding="utf-8",
            )
            with patch(
                "sys.argv",
                [
                    "run_eval.py",
                    "--agent",
                    "naive",
                    "--scenario",
                    "service-selector-mismatch",
                    "--seed",
                    "17",
                    "--seeds",
                    str(seeds_path),
                    "--output",
                    str((Path(temp_dir) / "results.jsonl")),
                ],
            ):
                args = run_eval.parse_args()
            seed_plan = run_eval.load_seed_plan(Path(args.seeds))
            scenario_filter = set(args.scenario)
            if scenario_filter:
                existing_ids = {entry.get("id") for entry in seed_plan}
                for scenario_id in sorted(scenario_filter):
                    if scenario_id not in existing_ids:
                        seed_plan.append({"id": scenario_id, "seeds": args.seed or [0]})

            self.assertIn(
                {"id": "service-selector-mismatch", "seeds": [17]},
                seed_plan,
            )


class RunEpisodeTests(unittest.TestCase):
    def test_run_episode_continues_after_workaround_until_oracle_success(self) -> None:
        instance = sample_scenario(load_catalog(), seed=11, scenario_id="bad-rollout-wrong-redis-host")

        class FakeAgent:
            name = "llm"

            def __init__(self) -> None:
                self.actions = iter(
                    [
                        "kubectl -n tron set env deployment/nginx REDIS_HOST=redis",
                        "kubectl -n tron patch configmap app-config -p '{\"data\":{\"REDIS_HOST\":\"redis\"}}'",
                    ]
                )

            def next_action(self, instance, observation, history):
                del instance, observation, history
                return next(self.actions)

        class FakeVerdict:
            def __init__(self, value: str) -> None:
                self.value = value

        class FakeEvaluation:
            def __init__(self, verdict: str, score: float) -> None:
                self.verdict = FakeVerdict(verdict)
                self.score = score
                self.summary = verdict
                self.checks = []

        class FakeStep:
            def __init__(self, command: str, reward: float) -> None:
                self.command = command
                self.reward = reward
                self.return_code = 0
                self.stdout = ""
                self.stderr = ""

        class FakeTransition:
            def __init__(self, score: float) -> None:
                self.reward = 0.0
                self.done = True
                self.service_score = score
                self.info = {"rejected": False, "timed_out": False, "action_cost": 0.0}
                self.observation = make_observation()
                self.observation.service_probe.score = score
                self.observation.service_probe.data_status = "ok"
                self.observation.service_probe.http_status = 200

        class FakeEnv:
            def __init__(self) -> None:
                self.current_instance = instance
                self.config = BenchmarkConfig(max_agent_steps=4)
                self.done = False
                self.step_number = 0
                self.steps = []

            def reset(self, scenario_id=None, seed=None, hard_reset=False):
                del scenario_id, seed, hard_reset
                observation = make_observation()
                observation.service_probe.score = 0.7
                return observation

            def step(self, action: str):
                self.step_number += 1
                self.steps.append(FakeStep(action, 0.0))
                self.done = True
                return FakeTransition(1.0)

            def evaluate(self, instance, steps):
                del instance, steps
                if len(self.steps) == 1:
                    return FakeEvaluation("failure", 0.5)
                return FakeEvaluation("success", 1.0)

        with patch("eval.run_eval.probe_service", return_value=ServiceProbe("ok", "ok", 200, 90, 1.0)):
            record = run_eval.run_episode(
                FakeEnv(),
                FakeAgent(),
                scenario_id="bad-rollout-wrong-redis-host",
                seed=11,
            )

        self.assertEqual(record["step_count"], 2)
        self.assertEqual(record["oracle"]["verdict"], "success")


if __name__ == "__main__":
    unittest.main()
