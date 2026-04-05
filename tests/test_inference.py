from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from inference import OpenAIPlanner, build_prompt, emit, parse_planner_response
from tron_openenv.models import ClusterSummaryView, ServiceProbeView, TronObservation, TronTask


class InferenceHelpersTests(unittest.TestCase):
    def test_build_prompt_includes_task_and_observation(self) -> None:
        task = TronTask(
            id="easy",
            title="Selector repair",
            difficulty="easy",
            scenario_id="service-selector-mismatch",
            description="repair selector",
            default_seed=11,
            max_agent_steps=12,
        )
        observation = TronObservation(
            task_id="easy",
            scenario_id="service-selector-mismatch",
            step_count=1,
            incident_brief="service degraded",
            last_action="kubectl -n tron get pods",
            last_reward=0.0,
            service_probe=ServiceProbeView(
                health_status="ok",
                data_status="error",
                http_status=500,
                latency_ms=42,
                score=0.7,
            ),
            cluster_summary=ClusterSummaryView(
                pods="nginx running",
                services="redis mismatch",
                deployments="nginx 1/1",
                endpoints="redis <none>",
            ),
            recent_change_hint="recent selector change",
            done=False,
        )
        prompt = build_prompt(task, observation, history=[])
        self.assertIn('"task"', prompt)
        self.assertIn('"service_probe"', prompt)
        self.assertIn("Return the next highest-value benchmark command", prompt)

    def test_emit_outputs_tagged_json(self) -> None:
        with patch("builtins.print") as mocked_print:
            emit("STEP", {"task_id": "easy", "step": 1})
        rendered = mocked_print.call_args.args[0]
        self.assertTrue(rendered.startswith("[STEP] "))
        self.assertEqual(json.loads(rendered[len("[STEP] ") :]), {"task_id": "easy", "step": 1})

    def test_openai_planner_uses_required_env_style_inputs(self) -> None:
        mock_client = unittest.mock.Mock()
        mock_client.chat.completions.create.return_value.choices = [
            unittest.mock.Mock(message=unittest.mock.Mock(content='{"intent":"inspect","command":"kubectl -n tron get pods"}'))
        ]
        with patch("inference.OpenAI", return_value=mock_client):
            planner = OpenAIPlanner(api_base_url="https://example.com/v1", model_name="demo-model", api_key="token")
            result = planner.complete("sys", "user")
        self.assertIn('"command":"kubectl -n tron get pods"', result)

    def test_parse_planner_response_truncates_overlong_intent(self) -> None:
        proposal = parse_planner_response(
            '{"intent":"inspect the current redis service selector and pod labels before deciding the durable repair","command":"kubectl -n tron get svc redis -o yaml"}'
        )

        self.assertEqual(proposal.command, "kubectl -n tron get svc redis -o yaml")
        self.assertLessEqual(len(proposal.intent.split()), 12)

    def test_parse_planner_response_recovers_command_from_multiline_output(self) -> None:
        proposal = parse_planner_response(
            "Intent: inspect cluster state first\nkubectl -n tron get pods"
        )

        self.assertEqual(proposal.intent, "execute next benchmark action")
        self.assertEqual(proposal.command, "kubectl -n tron get pods")


if __name__ == "__main__":
    unittest.main()
