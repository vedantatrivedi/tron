from __future__ import annotations

from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class OpenEnvContractTests(unittest.TestCase):
    def test_openenv_yaml_references_live_paths(self) -> None:
        payload = yaml.safe_load((ROOT / "openenv.yaml").read_text(encoding="utf-8"))

        self.assertEqual(payload["name"], "tron")
        self.assertEqual(payload["scenario_sampling"]["catalog"], "tron/scenario_catalog.py")
        self.assertEqual(payload["evaluation"]["oracle"], "tron/oracle.py")
        self.assertEqual(payload["runtime"]["reset_strategy"], "in_cluster_restore")
        self.assertEqual(payload["entrypoints"]["server"], "python -m tron_openenv.server.app")
        self.assertEqual(payload["entrypoints"]["inference"], "python inference.py")
        self.assertEqual([task["id"] for task in payload["tasks"]], ["easy", "medium", "hard"])
        self.assertEqual(
            [task["grader"] for task in payload["tasks"]],
            [
                "graders.tron_graders:grade_easy",
                "graders.tron_graders:grade_medium",
                "graders.tron_graders:grade_hard",
            ],
        )

    def test_openenv_referenced_files_exist(self) -> None:
        payload = yaml.safe_load((ROOT / "openenv.yaml").read_text(encoding="utf-8"))

        self.assertTrue((ROOT / payload["scenario_sampling"]["catalog"]).exists())
        self.assertTrue((ROOT / payload["evaluation"]["oracle"]).exists())
        self.assertTrue((ROOT / "inference.py").exists())
        self.assertTrue((ROOT / "server" / "app.py").exists())
        self.assertTrue((ROOT / "tron_openenv" / "server" / "app.py").exists())
        self.assertTrue((ROOT / "tron_openenv" / "client.py").exists())
        self.assertTrue((ROOT / "graders" / "tron_graders.py").exists())

    def test_openenv_local_tooling_is_present(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("openenv-install:", makefile)
        self.assertIn("openenv-check:", makefile)
        self.assertIn("OPENENV_REF ?= c719decf2b19175d5ca35301d58a14c83e985480", makefile)
        self.assertTrue((ROOT / "scripts" / "openenv_check.sh").exists())
        self.assertTrue((ROOT / "scripts" / "space_smoke.sh").exists())

    def test_ci_runs_openenv_validation(self) -> None:
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("ci: test openenv-check", makefile)
        self.assertIn("- name: Install OpenEnv CLI", workflow)
        self.assertIn("run: make openenv-install", workflow)
        self.assertIn("run: make ci", workflow)


if __name__ == "__main__":
    unittest.main()
