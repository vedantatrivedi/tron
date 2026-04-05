from __future__ import annotations

"""Deterministic reviewer-facing demo flow for tron."""

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.run_eval import _to_jsonable, print_compact_summary, run_episode
from tron.env import TronEnvironment
from tron.models import BenchmarkConfig, ClusterConfig


@dataclass(frozen=True)
class DemoStep:
    intent: str
    command: str


class ScriptedDemoAgent:
    name = "demo"

    def __init__(self, steps: list[DemoStep]) -> None:
        self.steps = steps
        self.cursor = 0
        self.last_intent: str | None = None

    def next_action(self, instance, observation, history):  # noqa: D401
        del instance, observation, history
        if self.cursor >= len(self.steps):
            return None
        step = self.steps[self.cursor]
        self.cursor += 1
        self.last_intent = step.intent
        return step.command


def build_demo_steps(scenario_id: str) -> list[DemoStep]:
    playbooks = {
        "service-selector-mismatch": [
            DemoStep("inspect redis service selector", "kubectl -n tron get service redis -o yaml"),
            DemoStep("confirm backend endpoints are empty", "kubectl -n tron get endpoints redis -o yaml"),
            DemoStep(
                "restore redis service selector",
                "kubectl -n tron patch service redis --type merge -p '{\"spec\":{\"selector\":{\"app\":\"redis\"}}}'",
            ),
        ],
        "bad-rollout-wrong-redis-host": [
            DemoStep("inspect source config", "kubectl -n tron get configmap app-config -o yaml"),
            DemoStep(
                "restore redis host in config",
                "kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"redis\"}}'",
            ),
            DemoStep("refresh nginx rollout", "kubectl -n tron rollout restart deployment/nginx"),
            DemoStep("verify rollout completion", "kubectl -n tron rollout status deployment/nginx"),
        ],
        "networkpolicy-blocks-nginx-to-redis": [
            DemoStep("inspect namespace policies", "kubectl -n tron get networkpolicy -o yaml"),
            DemoStep(
                "remove likely deny policy",
                "kubectl -n tron delete networkpolicy deny-nginx-egress --ignore-not-found",
            ),
            DemoStep(
                "remove alternate deny policy name",
                "kubectl -n tron delete networkpolicy block-redis-egress --ignore-not-found",
            ),
        ],
    }
    if scenario_id not in playbooks:
        raise KeyError(f"unsupported demo scenario: {scenario_id}")
    return playbooks[scenario_id]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic tron reviewer demo.")
    parser.add_argument(
        "--scenario",
        choices=sorted(
            ["service-selector-mismatch", "bad-rollout-wrong-redis-host", "networkpolicy-blocks-nginx-to-redis"]
        ),
        default="service-selector-mismatch",
    )
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--cluster-name", default="tron-lab")
    parser.add_argument("--ingress-port", type=int, default=8080)
    parser.add_argument("--hard-reset", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "eval" / "demo-run.jsonl"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BenchmarkConfig(
        random_seed=args.seed,
        max_agent_steps=len(build_demo_steps(args.scenario)) + 2,
        work_dir=ROOT,
        cluster=ClusterConfig(
            cluster_name=args.cluster_name,
            namespace="tron",
            ingress_host="tron.localhost",
            ingress_port=args.ingress_port,
        ),
    )
    env = TronEnvironment(config)
    agent = ScriptedDemoAgent(build_demo_steps(args.scenario))
    record = run_episode(
        env,
        agent,
        scenario_id=args.scenario,
        seed=args.seed,
        hard_reset=args.hard_reset,
    )
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, default=_to_jsonable) + "\n", encoding="utf-8")
    print("demo_summary=" + json.dumps(
        {
            "scenario_id": record["scenario_id"],
            "oracle_score": record["oracle"]["score"],
            "verdict": record["oracle"]["verdict"],
            "step_count": record["step_count"],
        },
        sort_keys=True,
    ))
    print_compact_summary(record)


if __name__ == "__main__":
    main()
