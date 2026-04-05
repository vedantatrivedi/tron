from __future__ import annotations

import pathlib
import unittest

from tron.scenario_catalog import load_catalog


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RuntimeScaffoldTests(unittest.TestCase):
    def test_test_client_uses_local_ingress_port(self) -> None:
        content = (ROOT / "app" / "test_client.sh").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8080", content)
        self.assertIn('/health', content)
        self.assertIn('/data', content)

    def test_setup_exposes_ingress_on_8080_and_verifies_backend_path(self) -> None:
        content = (ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn('--port "${INGRESS_PORT}:80@loadbalancer"', content)
        self.assertIn('wait_for_http "/health" "ok"', content)
        self.assertIn('wait_for_http "/data" "\\"value\\": \\"baseline\\""', content)

    def test_setup_only_pulls_images_when_missing_and_imports_them_into_k3d(self) -> None:
        content = (ROOT / "setup.sh").read_text(encoding="utf-8")
        self.assertIn('docker image inspect "${image}"', content)
        self.assertIn('docker pull "${image}"', content)
        self.assertIn('k3d image import -c "${CLUSTER_NAME}" "${IMAGES[@]}"', content)

    def test_nginx_config_separates_health_and_data_paths(self) -> None:
        content = (ROOT / "app" / "nginx.conf").read_text(encoding="utf-8")
        self.assertIn("location = /health", content)
        self.assertIn("return 200 \"ok\\n\";", content)
        self.assertIn("location = /data", content)
        self.assertIn("proxy_pass http://redis_bridge/data;", content)

    def test_runtime_manifests_include_bridge_and_ingress_class(self) -> None:
        nginx_manifest = (ROOT / "manifests" / "nginx.yaml").read_text(encoding="utf-8")
        ingress_manifest = (ROOT / "manifests" / "ingress.yaml").read_text(encoding="utf-8")
        self.assertIn("name: redis-bridge", nginx_manifest)
        self.assertIn("containerPort: 9000", nginx_manifest)
        self.assertIn("ingressClassName: traefik", ingress_manifest)

    def test_bridge_config_supports_resource_pressure_knobs(self) -> None:
        configmap = (ROOT / "manifests" / "configmap.yaml").read_text(encoding="utf-8")
        self.assertIn("BRIDGE_CPU_BURN_MS", configmap)
        self.assertIn("BRIDGE_MEMORY_BURST_MB", configmap)
        self.assertIn("def induce_request_pressure()", configmap)
        self.assertIn("BRIDGE_CPU_BURN_MS = int", configmap)
        self.assertIn("BRIDGE_MEMORY_BURST_MB = int", configmap)

    def test_scenarios_target_local_blackbox_endpoint(self) -> None:
        catalog = load_catalog()
        for scenario in catalog:
            self.assertEqual(scenario.blackbox_url, "http://127.0.0.1:8080/data")

    def test_ingress_drift_scenario_matches_current_route_shape(self) -> None:
        scenario = next(
            template
            for template in load_catalog()
            if template.scenario_id == "ingress-path-rewrite-bug"
        )
        patched_config = scenario.inject_commands[0]
        self.assertIn("patch ingress tron-ingress", patched_config)
        self.assertIn("/spec/rules/0/http/paths/0/path", patched_config)


if __name__ == "__main__":
    unittest.main()
