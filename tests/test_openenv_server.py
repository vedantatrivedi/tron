from __future__ import annotations

import io
import importlib
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fastapi.testclient import TestClient

from inference import run_task
from tron.models import (
    AgentStep,
    AgentVerdict,
    BenchmarkConfig,
    CheckResult,
    ClusterConfig,
    ClusterSummary,
    EvaluationRecord,
    ObservationBundle,
    ScenarioInstance,
    ScenarioKind,
    ScenarioTemplate,
    ServiceProbe,
    StepTransition,
)
from tron_openenv.client import TronEnvClient
from tron_openenv.models import ResetRequest, TronAction, TronTask
from tron_openenv.server.app import create_app
from tron_openenv.server.environment import TASKS, TronOpenEnvService


def _template(scenario_id: str = "service-selector-mismatch") -> ScenarioTemplate:
    return ScenarioTemplate(
        id=scenario_id,
        kind=ScenarioKind.SERVICE,
        title="Fake scenario",
        trigger_context="ctx",
        user_visible_symptom="symptom",
        hidden_faults=[],
        distractors=[],
        difficulty="easy",
        parameters={},
        inject_commands=[],
        activation_checks=[],
        restore_commands=[],
        repair_checks=[],
    )


class FakeCoreEnv:
    def __init__(self, *, repair_complete: bool = True, executor=None) -> None:
        self.config = BenchmarkConfig(
            max_agent_steps=12,
            work_dir=".",  # type: ignore[arg-type]
            cluster=ClusterConfig(namespace="tron", ingress_host="tron.localhost", ingress_port=8080),
        )
        self.executor = executor
        self.repair_complete = repair_complete
        self.current_instance: ScenarioInstance | None = None
        self.current_observation: ObservationBundle | None = None
        self.current_service_score = 0.0
        self.last_reward = 0.0
        self.step_number = 0
        self.done = False
        self.steps: list[AgentStep] = []

    def reset(self, scenario_id: str | None = None, seed: int | None = None, hard_reset: bool = False) -> ObservationBundle:
        del hard_reset
        scenario_id = scenario_id or "service-selector-mismatch"
        self.current_instance = ScenarioInstance(
            template=_template(scenario_id),
            seed=seed or 11,
            chosen_parameters={},
            rendered_inject_commands=[],
            rendered_distractor_commands=[],
            rendered_restore_commands=[],
            rendered_distractor_restore_commands=[],
            recent_changes=["recent hint"],
        )
        self.step_number = 0
        self.done = False
        self.steps = []
        self.last_reward = 0.0
        self.current_service_score = 0.7
        self.current_observation = ObservationBundle(
            incident_brief="service degraded",
            step_number=0,
            last_action=None,
            last_reward=0.0,
            service_probe=ServiceProbe(
                health_status="ok",
                data_status="error",
                http_status=500,
                latency_ms=50,
                score=0.7,
            ),
            cluster_summary=ClusterSummary(
                pods="nginx running",
                services="redis selector mismatch",
                deployments="nginx 1/1",
                endpoints="redis <none>",
            ),
            recent_change_hint="recent hint",
        )
        return self.current_observation

    def step(self, action: str) -> StepTransition:
        self.step_number += 1
        reward = 0.3
        self.last_reward = reward
        self.current_service_score = 1.0
        self.done = True
        self.steps.append(AgentStep(command=action, return_code=0, stdout="patched", stderr="", reward=reward))
        self.current_observation = ObservationBundle(
            incident_brief="service degraded",
            step_number=self.step_number,
            last_action=action,
            last_reward=reward,
            service_probe=ServiceProbe(
                health_status="ok",
                data_status="ok",
                http_status=200,
                latency_ms=20,
                score=1.0,
            ),
            cluster_summary=ClusterSummary(
                pods="nginx running",
                services="redis fixed",
                deployments="nginx 1/1",
                endpoints="redis 10.0.0.12:6379",
            ),
            recent_change_hint="recent hint",
        )
        return StepTransition(
            observation=self.current_observation,
            reward=reward,
            done=True,
            service_score=1.0,
            info={"rejected": False, "timed_out": False, "action_cost": 0.0},
        )

    def evaluate(self, instance: ScenarioInstance, steps: list[AgentStep]) -> EvaluationRecord:
        del instance
        verdict = AgentVerdict.SUCCESS if self.repair_complete else AgentVerdict.FAILURE
        score = 1.0 if self.repair_complete else 0.5
        summary = "complete" if self.repair_complete else "incomplete"
        return EvaluationRecord(
            scenario_id=self.current_instance.template.scenario_id,  # type: ignore[union-attr]
            seed=self.current_instance.seed,  # type: ignore[union-attr]
            verdict=verdict,
            score=score,
            summary=summary,
            chosen_parameters={},
            checks=[CheckResult(name="repair", ok=self.repair_complete, details=summary)],
            observations=self.current_observation,  # type: ignore[arg-type]
            steps=steps,
        )


class StaticPlanner:
    def __init__(self, response: str = '{"intent":"repair service","command":"kubectl -n tron get service redis -o yaml"}') -> None:
        self.response = response

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        return self.response


class OpenEnvServerTests(unittest.TestCase):
    def test_server_config_supports_space_specific_timeout_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRON_OPENENV_BLACKBOX_TIMEOUT_SECONDS": "1.5",
                "TRON_OPENENV_TRUSTED_TIMEOUT_SECONDS": "45",
                "TRON_OPENENV_ROLLOUT_TIMEOUT_SECONDS": "30",
                "TRON_OPENENV_MUTATION_SETTLE_SECONDS": "0.25",
            },
            clear=False,
        ):
            from tron_openenv.server.environment import _build_config

            config = _build_config(max_agent_steps=12)

        self.assertEqual(config.blackbox_timeout_seconds, 1.5)
        self.assertEqual(config.trusted_timeout_seconds, 45.0)
        self.assertEqual(config.rollout_status_timeout_seconds, 30)
        self.assertEqual(config.mutation_settle_seconds, 0.25)

    def test_repo_root_app_is_a_compatibility_shim(self) -> None:
        root_app_module = importlib.import_module("app")

        self.assertIs(root_app_module.app, importlib.import_module("tron_openenv.server.app").app)
        self.assertIs(root_app_module.create_app, create_app)

    def test_service_lists_three_curated_tasks(self) -> None:
        service = TronOpenEnvService(env=FakeCoreEnv())
        task_ids = [task.id for task in service.list_tasks()]
        self.assertEqual(task_ids, ["easy", "medium", "hard"])

    def test_http_metadata_endpoints_expose_tasks(self) -> None:
        app = create_app(TronOpenEnvService(env=FakeCoreEnv()))
        client = TestClient(app)

        root_response = client.get("/")
        self.assertEqual(root_response.status_code, 200)
        self.assertEqual(root_response.json()["name"], "tron")
        self.assertEqual([item["id"] for item in root_response.json()["tasks"]], ["easy", "medium", "hard"])

        info_response = client.get("/info")
        self.assertEqual(info_response.status_code, 200)
        self.assertEqual(info_response.json()["status"], "ok")
        self.assertEqual([item["id"] for item in info_response.json()["tasks"]], ["easy", "medium", "hard"])

        health_response = client.get("/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json(), {"status": "ok"})

    def test_http_reset_step_and_state_flow(self) -> None:
        app = create_app(TronOpenEnvService(env=FakeCoreEnv()))
        client = TestClient(app)

        tasks_response = client.get("/tasks")
        self.assertEqual(tasks_response.status_code, 200)
        self.assertEqual([item["id"] for item in tasks_response.json()], ["easy", "medium", "hard"])

        reset_response = client.post("/reset", json={"task_id": "easy", "seed": 11})
        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(reset_response.json()["task"]["id"], "easy")

        step_response = client.post("/step", json=TronAction(command="kubectl -n tron get service redis -o yaml").model_dump())
        self.assertEqual(step_response.status_code, 200)
        self.assertTrue(step_response.json()["done"])
        self.assertEqual(step_response.json()["info"]["oracle_verdict"], "success")

        state_response = client.get("/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["oracle_score"], 1.0)

    def test_http_reset_without_body_uses_default_request(self) -> None:
        app = create_app(TronOpenEnvService(env=FakeCoreEnv()))
        client = TestClient(app)

        reset_response = client.post("/reset")

        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(reset_response.json()["task"]["id"], "easy")

    def test_repair_incomplete_keeps_episode_open(self) -> None:
        service = TronOpenEnvService(env=FakeCoreEnv(repair_complete=False))
        service.reset(ResetRequest(task_id="easy", seed=11))
        result = service.step(TronAction(command="kubectl -n tron get service redis -o yaml"))
        self.assertFalse(result.done)
        self.assertFalse(result.info["repair_complete"])

    def test_cluster_precheck_cache_skips_repeated_kubectl_probe_within_ttl(self) -> None:
        class CountingExecutor:
            def __init__(self) -> None:
                self.calls = 0

            def run_argv(self, argv, timeout=20.0):
                del argv, timeout
                self.calls += 1
                return type("Result", (), {"return_code": 0, "stderr": "", "stdout": "ok"})()

        executor = CountingExecutor()
        service = TronOpenEnvService(env=FakeCoreEnv(executor=executor))
        service.cluster_check_ttl_seconds = 60.0
        service._last_cluster_check_monotonic = 0.0

        with patch("tron_openenv.server.environment.time.monotonic", return_value=10.0):
            service._assert_cluster_reachable()

        self.assertEqual(executor.calls, 0)

    def test_inference_logs_structured_tags(self) -> None:
        session = TestClient(create_app(TronOpenEnvService(env=FakeCoreEnv())))
        client = TronEnvClient(base_url="http://testserver", session=session)
        output = io.StringIO()

        with redirect_stdout(output):
            summary = run_task(client, StaticPlanner(), task_id="easy", seed=11)

        rendered = output.getvalue().strip().splitlines()
        self.assertTrue(rendered[0].startswith("[START] "))
        self.assertTrue(rendered[1].startswith("[STEP] "))
        self.assertTrue(rendered[-1].startswith("[END] "))
        self.assertEqual(summary["oracle_verdict"], "success")


if __name__ == "__main__":
    unittest.main()
