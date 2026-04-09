from __future__ import annotations

import argparse
import json
import os
import unittest
from unittest.mock import patch

from inference import (
    DEFAULT_API_BASE_URL,
    DEFAULT_MODEL_NAME,
    DEFAULT_RUNTIME_BASE_URL,
    OpenAIPlanner,
    build_env_client,
    build_prompt,
    emit_end,
    emit_start,
    emit_step,
    main,
    parse_planner_response,
    resolve_planner_config,
)
from tron_openenv.models import ClusterSummaryView, ServiceProbeView, TronObservation, TronTask


class InferenceHelpersTests(unittest.TestCase):
    def test_build_prompt_includes_task_and_observation(self) -> None:
        task = TronTask(
            id="easy",
            difficulty="easy",
            default_seed=11,
            max_agent_steps=12,
        )
        observation = TronObservation(
            task_id="easy",
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

    def test_emit_step_outputs_required_line_format(self) -> None:
        with patch("builtins.print") as mocked_print:
            emit_step(step=1, action="kubectl -n tron get pods", reward=0.0, done=False, error=None)
        rendered = mocked_print.call_args.args[0]
        self.assertEqual(
            rendered,
            "[STEP] step=1 action=kubectl -n tron get pods reward=0.00 done=false error=null",
        )

    def test_emit_start_and_end_use_required_line_format(self) -> None:
        with patch("builtins.print") as mocked_print:
            emit_start(task_name="easy", env_name="tron", model_name="gpt-5-mini")
            emit_end(success=True, steps=2, rewards=[0.0, 1.0])

        self.assertEqual(
            mocked_print.call_args_list[0].args[0],
            "[START] task=easy env=tron model=gpt-5-mini",
        )
        self.assertEqual(
            mocked_print.call_args_list[1].args[0],
            "[END] success=true steps=2 rewards=0.00,1.00",
        )

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

    def test_resolve_planner_config_prefers_openai_api_key_for_local_runs(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://example.com/v1",
                "OPENAI_API_KEY": "openai-key",
                "HF_TOKEN": "hf-token",
            },
            clear=True,
        ):
            api_base_url, model_name, api_key = resolve_planner_config()

        self.assertEqual(api_base_url, "https://example.com/v1")
        self.assertEqual(model_name, DEFAULT_MODEL_NAME)
        self.assertEqual(api_key, "openai-key")

    def test_resolve_planner_config_accepts_submission_style_variables(self) -> None:
        with patch.dict(
            os.environ,
            {
                "API_BASE_URL": "https://example.com/v1",
                "MODEL_NAME": "gpt-5-mini",
                "HF_TOKEN": "submission-key",
            },
            clear=True,
        ):
            api_base_url, model_name, api_key = resolve_planner_config()

        self.assertEqual(api_base_url, "https://example.com/v1")
        self.assertEqual(model_name, "gpt-5-mini")
        self.assertEqual(api_key, "submission-key")

    def test_resolve_planner_config_falls_back_to_hf_token(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "submission-key",
            },
            clear=True,
        ):
            _, _, api_key = resolve_planner_config()

        self.assertEqual(api_key, "submission-key")

    def test_resolve_planner_config_uses_required_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HF_TOKEN": "submission-key",
            },
            clear=True,
        ):
            api_base_url, model_name, api_key = resolve_planner_config()

        self.assertEqual(api_base_url, DEFAULT_API_BASE_URL)
        self.assertEqual(model_name, DEFAULT_MODEL_NAME)
        self.assertEqual(api_key, "submission-key")

    def test_build_env_client_defaults_to_live_space(self) -> None:
        client = build_env_client(None)
        try:
            self.assertEqual(client.base_url, DEFAULT_RUNTIME_BASE_URL)
        finally:
            client.close()

    def test_build_env_client_supports_explicit_local_mode(self) -> None:
        with patch("inference.TestClient", return_value=object()):
            client = build_env_client(None, local_env=True)
        self.assertEqual(client.base_url, "http://testserver")

    def test_main_emits_end_when_credentials_are_missing(self) -> None:
        args = argparse.Namespace(env_base_url="", local_env=False, hard_reset=False, task="easy", seed=11)
        with patch.dict(os.environ, {}, clear=True):
            with patch("inference.parse_args", return_value=args):
                with patch("builtins.print") as mocked_print:
                    main()

        stdout_lines = [call.args[0] for call in mocked_print.call_args_list if call.kwargs.get("file") is None]
        self.assertEqual(stdout_lines[0], f"[START] task=easy env=tron model={DEFAULT_MODEL_NAME}")
        self.assertEqual(stdout_lines[-1], "[END] success=false steps=0 rewards=")


if __name__ == "__main__":
    unittest.main()
