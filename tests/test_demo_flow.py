from __future__ import annotations

import unittest

from eval.demo import build_demo_steps, ScriptedDemoAgent


class DemoFlowTests(unittest.TestCase):
    def test_service_selector_demo_has_deterministic_repair_sequence(self) -> None:
        steps = build_demo_steps("service-selector-mismatch")

        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0].intent, "inspect redis service selector")
        self.assertIn("patch service redis", steps[-1].command)

    def test_scripted_demo_agent_emits_steps_in_order(self) -> None:
        agent = ScriptedDemoAgent(build_demo_steps("bad-rollout-wrong-redis-host"))

        first = agent.next_action(None, None, [])
        second = agent.next_action(None, None, [])

        self.assertIn("configmap app-config", first)
        self.assertIn("patch configmap app-config", second)
        self.assertEqual(agent.last_intent, "restore redis host in config")


if __name__ == "__main__":
    unittest.main()
