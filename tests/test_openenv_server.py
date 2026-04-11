from __future__ import annotations

import io
import importlib
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fastapi.testclient import TestClient

from inference import emit_end, emit_start, run_task
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
from tron_openenv.models import ResetRequest, TronAction, TronGradeResponse, TronTask
from tron_openenv.server.app import create_app
from tron_openenv.server.environment import (
    DEFAULT_REMOTE_INGRESS_HOST,
    DEFAULT_REMOTE_INGRESS_HOST_HEADER,
    DEFAULT_REMOTE_INGRESS_PORT,
    TASKS,
    TronOpenEnvService,
)


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
    def __init__(
        self,
        *,
        repair_complete: bool = True,
        executor=None,
        reset_delay_seconds: float = 0.0,
        reset_probe: ServiceProbe | None = None,
        observe_probes: list[ServiceProbe] | None = None,
    ) -> None:
        self.config = BenchmarkConfig(
            max_agent_steps=12,
            work_dir=".",  # type: ignore[arg-type]
            cluster=ClusterConfig(namespace="tron", ingress_host="tron.localhost", ingress_port=8080),
        )
        self.executor = executor
        self.repair_complete = repair_complete
        self.reset_delay_seconds = reset_delay_seconds
        self.reset_probe = reset_probe or ServiceProbe(
            health_status="ok",
            data_status="error",
            http_status=500,
            latency_ms=50,
            score=0.7,
        )
        self.observe_probes = list(observe_probes or [])
        self.current_instance: ScenarioInstance | None = None
        self.current_observation: ObservationBundle | None = None
        self.current_service_score = 0.0
        self.last_reward = 0.0
        self.step_number = 0
        self.done = False
        self.steps: list[AgentStep] = []

    def reset(self, scenario_id: str | None = None, seed: int | None = None, hard_reset: bool = False) -> ObservationBundle:
        del hard_reset
        if self.reset_delay_seconds:
            time.sleep(self.reset_delay_seconds)
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
        self.current_service_score = self.reset_probe.score
        self.current_observation = ObservationBundle(
            incident_brief="service degraded",
            step_number=0,
            last_action=None,
            last_reward=0.0,
            service_probe=self.reset_probe,
            cluster_summary=ClusterSummary(
                pods="nginx running",
                services="redis selector mismatch",
                deployments="nginx 1/1",
                endpoints="redis <none>",
            ),
            recent_change_hint="recent hint",
        )
        return self.current_observation

    def observe(
        self,
        instance: ScenarioInstance,
        step_number: int = 0,
        last_action: str | None = None,
        last_reward: float = 0.0,
        include_cluster_summary: bool = True,
    ) -> ObservationBundle:
        del instance, step_number, last_action, last_reward, include_cluster_summary
        probe = self.observe_probes.pop(0) if self.observe_probes else self.current_observation.service_probe
        self.current_service_score = probe.score
        self.current_observation = ObservationBundle(
            incident_brief="service degraded",
            step_number=self.step_number,
            last_action=None,
            last_reward=self.last_reward,
            service_probe=probe,
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
    def test_server_cluster_config_defaults_to_remote_ingress_when_env_vars_are_missing(self) -> None:
        with patch.dict(
            os.environ,
            {
                "INGRESS_HOST": "",
                "INGRESS_URL_HOST": "",
                "INGRESS_HOST_HEADER": "",
                "INGRESS_PORT": "",
            },
            clear=False,
        ):
            from tron_openenv.server.environment import _build_cluster_config

            cluster = _build_cluster_config()

        self.assertEqual(cluster.ingress_host, DEFAULT_REMOTE_INGRESS_HOST_HEADER)
        self.assertEqual(cluster.ingress_url_host, DEFAULT_REMOTE_INGRESS_HOST)
        self.assertEqual(cluster.ingress_port, DEFAULT_REMOTE_INGRESS_PORT)

    def test_server_config_supports_space_specific_timeout_overrides(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TRON_OPENENV_BLACKBOX_TIMEOUT_SECONDS": "1.5",
                "TRON_OPENENV_TRUSTED_TIMEOUT_SECONDS": "45",
                "TRON_OPENENV_ROLLOUT_TIMEOUT_SECONDS": "30",
                "TRON_OPENENV_MUTATION_SETTLE_SECONDS": "0.25",
                "TRON_OPENENV_TRANSIENT_PROBE_WAIT_SECONDS": "0.5",
                "TRON_OPENENV_SKIP_RESET_VALIDATION": "1",
                "TRON_OPENENV_SKIP_RESET_CLUSTER_SUMMARY": "1",
            },
            clear=False,
        ):
            from tron_openenv.server.environment import _build_config

            config = _build_config(max_agent_steps=12)

        self.assertEqual(config.blackbox_timeout_seconds, 1.5)
        self.assertEqual(config.trusted_timeout_seconds, 45.0)
        self.assertEqual(config.rollout_status_timeout_seconds, 30)
        self.assertEqual(config.mutation_settle_seconds, 0.25)
        self.assertEqual(config.transient_probe_wait_seconds, 0.5)
        self.assertTrue(config.skip_reset_validation)
        self.assertTrue(config.skip_reset_cluster_summary)

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
        self.assertTrue(all(item["grader"] for item in root_response.json()["tasks"]))

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
        self.assertTrue(all(item["grader"] for item in tasks_response.json()))

        reset_response = client.post("/reset", json={"task_id": "easy", "seed": 11})
        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(reset_response.json()["task"]["id"], "easy")

        grade_response = client.post("/grader/easy")
        self.assertEqual(grade_response.status_code, 200)
        self.assertEqual(grade_response.json()["task_id"], "easy")
        self.assertGreater(grade_response.json()["score"], 0.0)
        self.assertLess(grade_response.json()["score"], 1.0)

        step_response = client.post("/step", json=TronAction(command="kubectl -n tron get service redis -o yaml").model_dump())
        self.assertEqual(step_response.status_code, 200)
        self.assertTrue(step_response.json()["done"])
        self.assertEqual(step_response.json()["info"]["oracle_verdict"], "success")

        state_response = client.get("/state")
        self.assertEqual(state_response.status_code, 200)
        self.assertEqual(state_response.json()["oracle_score"], 1.0)

    def test_http_grade_proxies_to_remote_when_cluster_is_unreachable_without_kubeconfig(self) -> None:
        class FailingExecutor:
            def run_argv(self, argv, timeout=20.0):
                del argv, timeout
                return type("Result", (), {"return_code": 1, "stderr": "cluster unreachable", "stdout": ""})()

        service = TronOpenEnvService(env=FakeCoreEnv(executor=FailingExecutor()))
        app = create_app(service)
        client = TestClient(app)

        with patch.dict("os.environ", {"KUBECONFIG_B64": "", "KUBECONFIG": ""}, clear=False):
            with patch.object(
                TronOpenEnvService,
                "_grade_via_remote_runtime",
                return_value=TronGradeResponse(
                    task_id="easy",
                    score=0.7,
                    reward=0.7,
                    episode_id="remote-episode",
                    step_count=0,
                    done=False,
                ),
            ) as remote_grade:
                response = client.get("/grader/easy")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["score"], 0.7)
        remote_grade.assert_called_once_with("easy", seed=None)

    def test_http_reset_without_body_uses_default_request(self) -> None:
        app = create_app(TronOpenEnvService(env=FakeCoreEnv()))
        client = TestClient(app)

        reset_response = client.post("/reset")

        self.assertEqual(reset_response.status_code, 200)
        self.assertEqual(reset_response.json()["task"]["id"], "easy")

    @patch("tron_openenv.server.environment.time.sleep", return_value=None)
    def test_http_reset_waits_for_task_score_to_enter_open_interval(self, _sleep_mock) -> None:
        app = create_app(
            TronOpenEnvService(
                env=FakeCoreEnv(
                    reset_probe=ServiceProbe("unreachable", "unreachable", None, None, 0.0),
                    observe_probes=[ServiceProbe("ok", "error", 503, 250, 0.7)],
                )
            )
        )
        client = TestClient(app)

        reset_response = client.post("/reset", json={"task_id": "medium", "seed": 13})

        self.assertEqual(reset_response.status_code, 200)
        probe = reset_response.json()["observation"]["service_probe"]
        self.assertEqual(probe["health_status"], "ok")
        self.assertEqual(probe["data_status"], "error")
        self.assertEqual(probe["score"], 0.7)

    def test_http_reset_async_can_be_polled_until_completed(self) -> None:
        app = create_app(TronOpenEnvService(env=FakeCoreEnv(reset_delay_seconds=0.05)))
        client = TestClient(app)

        start_response = client.post("/reset_async")
        self.assertEqual(start_response.status_code, 200)
        job = start_response.json()
        self.assertEqual(job["status"], "running")

        job_id = job["job_id"]
        deadline = time.time() + 1.0
        last_status = job
        while time.time() < deadline:
            status_response = client.get(f"/reset_async/{job_id}")
            self.assertEqual(status_response.status_code, 200)
            last_status = status_response.json()
            if last_status["status"] == "completed":
                break
            time.sleep(0.01)

        self.assertEqual(last_status["status"], "completed")
        self.assertEqual(last_status["result"]["task"]["id"], "easy")
        self.assertIsNone(last_status["error"])

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
            emit_start(task_name="easy", env_name="tron", model_name="gpt-5-mini")
            summary = run_task(client, StaticPlanner(), task_id="easy", seed=11)
            emit_end(success=summary["success"], steps=summary["steps"], rewards=summary["rewards"])

        rendered = output.getvalue().strip().splitlines()
        self.assertEqual(rendered[0], "[START] task=easy env=tron model=gpt-5-mini")
        self.assertTrue(
            rendered[1].startswith(
                "[STEP] step=1 action=kubectl -n tron get service redis -o jsonpath={.spec.selector.app} "
                "reward=0.30 done=true error=null"
            )
        )
        self.assertEqual(rendered[-1], "[END] success=true steps=1 rewards=0.30")
        self.assertTrue(summary["success"])


if __name__ == "__main__":
    unittest.main()
