from __future__ import annotations

import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path

import requests

from tron.env import TronEnvironment
from tron.executor import CommandExecutor
from tron.incident_engine import IncidentEngine
from tron.models import BenchmarkConfig, ClusterConfig
from tron.sampler import sample_scenario
from tron.scenario_catalog import load_catalog


ROOT = Path(__file__).resolve().parents[1]
E2E_CLUSTER_NAME = os.environ.get("TRON_E2E_CLUSTER_NAME", "tron-e2e")
E2E_INGRESS_PORT = os.environ.get("TRON_E2E_INGRESS_PORT", "18080")
E2E_HOST = os.environ.get("TRON_E2E_HOST", "tron.localhost")
E2E_ENABLED = os.environ.get("TRON_RUN_E2E") == "1"


def _has_prereqs() -> bool:
    return all(shutil.which(binary) for binary in ("docker", "k3d", "kubectl", "curl"))


@unittest.skipUnless(
    E2E_ENABLED and _has_prereqs(),
    "set TRON_RUN_E2E=1 and install docker, k3d, kubectl, and curl to run live E2E tests",
)
class EndToEndBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.executor = CommandExecutor(cwd=str(ROOT))
        cls.engine = IncidentEngine(cls.executor)
        cls.base_env = {
            **os.environ,
            "CLUSTER_NAME": E2E_CLUSTER_NAME,
            "INGRESS_PORT": E2E_INGRESS_PORT,
            "INGRESS_HOST": E2E_HOST,
        }
        subprocess.run(["bash", "./cleanup.sh"], cwd=ROOT, env=cls.base_env, check=False)
        subprocess.run(["bash", "./setup.sh"], cwd=ROOT, env=cls.base_env, check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["bash", "./cleanup.sh"], cwd=ROOT, env=cls.base_env, check=False)

    def setUp(self) -> None:
        subprocess.run(["bash", "./setup.sh"], cwd=ROOT, env=self.base_env, check=True)

    def _assert_blackbox(self, path: str, expected_status: int, expected_fragment: str) -> None:
        response = requests.get(
            f"http://127.0.0.1:{E2E_INGRESS_PORT}{path}",
            headers={"Host": E2E_HOST},
            timeout=5.0,
        )
        self.assertEqual(response.status_code, expected_status)
        self.assertIn(expected_fragment, response.text)

    def _wait_for_blackbox(
        self,
        path: str,
        expected_statuses: set[int],
        expected_fragment: str,
        timeout_seconds: int = 90,
    ) -> requests.Response:
        deadline = time.time() + timeout_seconds
        last_error = ""
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"http://127.0.0.1:{E2E_INGRESS_PORT}{path}",
                    headers={"Host": E2E_HOST},
                    timeout=5.0,
                )
                if response.status_code in expected_statuses and expected_fragment in response.text:
                    return response
                last_error = f"status={response.status_code} body={response.text[:200]!r}"
            except requests.RequestException as exc:
                last_error = str(exc)
            time.sleep(3)
        self.fail(f"timed out waiting for {path}: {last_error}")

    def _wait_for_data_failure(self, timeout_seconds: int = 90) -> None:
        deadline = time.time() + timeout_seconds
        last_error = ""
        while time.time() < deadline:
            try:
                response = requests.get(
                    f"http://127.0.0.1:{E2E_INGRESS_PORT}/data",
                    headers={"Host": E2E_HOST},
                    timeout=5.0,
                )
                if response.status_code >= 500:
                    return
                last_error = f"status={response.status_code} body={response.text[:200]!r}"
            except requests.RequestException as exc:
                last_error = str(exc)
                return
            time.sleep(3)
        self.fail(f"timed out waiting for /data failure: {last_error}")

    def _wait_for_recovery(self) -> None:
        deadline = time.time() + 90
        last_error = ""
        while time.time() < deadline:
            try:
                self._assert_blackbox("/data", 200, '"value": "baseline"')
                return
            except (AssertionError, requests.RequestException) as exc:
                last_error = str(exc)
                time.sleep(3)
        self.fail(f"timed out waiting for baseline recovery: {last_error}")

    def test_single_root_cause_scenario_e2e(self) -> None:
        instance = sample_scenario(
            load_catalog(),
            seed=17,
            scenario_id="bad-rollout-wrong-redis-host",
        )

        self._assert_blackbox("/health", 200, "ok")
        self._assert_blackbox("/data", 200, '"value": "baseline"')

        self.engine.inject(instance)
        activation = self.engine.verify_activation(instance)
        self.assertTrue(all(check.ok for check in activation), activation)

        self._wait_for_blackbox("/health", {200}, "ok")
        self._wait_for_data_failure()

        self.engine.restore(instance)
        self._wait_for_recovery()

    def test_compound_scenario_e2e(self) -> None:
        instance = sample_scenario(
            load_catalog(),
            seed=5,
            scenario_id="networkpolicy-plus-secondary-drift",
        )

        self._assert_blackbox("/data", 200, '"value": "baseline"')

        self.engine.inject(instance)
        activation = self.engine.verify_activation(instance)
        self.assertTrue(all(check.ok for check in activation), activation)

        self._wait_for_data_failure()

        self.engine.restore(instance)
        self._wait_for_recovery()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(
    E2E_ENABLED and _has_prereqs(),
    "set TRON_RUN_E2E=1 and install docker, k3d, kubectl, and curl to run live E2E tests",
)
class EnvironmentLoopEndToEndTests(unittest.TestCase):
    def test_env_reset_and_step_repair_flow_e2e(self) -> None:
        cluster_name = E2E_CLUSTER_NAME
        ingress_port = int(E2E_INGRESS_PORT)
        base_env = {
            **os.environ,
            "CLUSTER_NAME": cluster_name,
            "INGRESS_PORT": str(ingress_port),
            "INGRESS_HOST": E2E_HOST,
        }
        try:
            env = TronEnvironment(
                BenchmarkConfig(
                    random_seed=17,
                    max_agent_steps=4,
                    work_dir=ROOT,
                    mutation_settle_seconds=1.0,
                    cluster=ClusterConfig(
                        cluster_name=cluster_name,
                        namespace="tron",
                        ingress_host=E2E_HOST,
                        ingress_port=ingress_port,
                    ),
                )
            )

            observation = env.reset(scenario_id="bad-rollout-wrong-redis-host", seed=17)
            self.assertEqual(observation.step_number, 0)
            self.assertEqual(observation.service_probe.health_status, "ok")
            self.assertLess(observation.service_probe.score, 1.0)

            first = env.step("kubectl apply -f manifests/configmap.yaml")
            self.assertFalse(first.done)

            second = env.step("kubectl -n tron rollout restart deployment/nginx")
            self.assertFalse(second.done)

            third = env.step("kubectl -n tron rollout status deployment/nginx --timeout=120s")
            if not third.done:
                fourth = env.step(
                    f"curl -fsS -H 'Host: {E2E_HOST}' http://127.0.0.1:{ingress_port}/data"
                )
                self.assertTrue(fourth.done)
                self.assertGreaterEqual(fourth.service_score, 1.0)
                self.assertEqual(fourth.observation.service_probe.data_status, "ok")
            else:
                self.assertGreaterEqual(third.service_score, 1.0)
                self.assertEqual(third.observation.service_probe.data_status, "ok")
        finally:
            subprocess.run(["bash", "./cleanup.sh"], cwd=ROOT, env=base_env, check=False)
