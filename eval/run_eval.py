from __future__ import annotations

"""Evaluation harness for running baseline agents over seeded scenarios."""

import argparse
from dataclasses import asdict, is_dataclass
from enum import Enum
import json
import sys
from pathlib import Path
from typing import Any, Protocol

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from baseline import llm_agent, naive
from env import TronEnvironment
from models import BenchmarkConfig, ClusterConfig
from oracle import probe_service


class Agent(Protocol):
    name: str

    def next_action(self, instance, observation, history: list[dict]) -> str | None:
        ...


def _to_jsonable(value: Any):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def load_seed_plan(path: Path) -> list[dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    scenarios = payload.get("scenarios", [])
    if scenarios:
        return scenarios

    legacy_seeds = payload.get("seeds", [])
    return [{"id": None, "seeds": legacy_seeds}]


def build_agent(name: str) -> Agent:
    if name == "naive":
        return naive.build_agent()
    if name == "llm":
        return llm_agent.build_agent()
    raise KeyError(f"unknown agent: {name}")


def classify_action(command: str) -> str:
    stripped = command.strip()
    if stripped.startswith("curl "):
        return "diagnostic"
    if stripped.startswith("kubectl get "):
        return "diagnostic"
    if stripped.startswith("kubectl describe "):
        return "diagnostic"
    if stripped.startswith("kubectl logs "):
        return "diagnostic"
    if stripped.startswith("kubectl top "):
        return "diagnostic"
    if " rollout history " in stripped:
        return "diagnostic"
    if stripped.startswith("kubectl exec "):
        return "diagnostic"
    return "destructive"


def _print_progress(message: str) -> None:
    print(message, flush=True)


def run_episode(
    env: TronEnvironment,
    agent: Agent,
    scenario_id: str | None,
    seed: int,
    hard_reset: bool = False,
) -> dict:
    _print_progress(
        f"[episode] agent={agent.name} scenario={scenario_id or 'sampled'} seed={seed} "
        f"reset=start hard_reset={str(hard_reset).lower()}"
    )
    initial_observation = env.reset(scenario_id=scenario_id, seed=seed, hard_reset=hard_reset)
    if env.current_instance is None:
        raise RuntimeError("environment did not produce a scenario instance")
    instance = env.current_instance
    _print_progress(
        f"[episode] agent={agent.name} scenario={instance.template.scenario_id} "
        f"seed={seed} reset=done initial_score={initial_observation.service_probe.score:.2f} "
        f"health={initial_observation.service_probe.health_status} "
        f"data={initial_observation.service_probe.data_status}"
    )

    history: list[dict] = []
    observation = initial_observation
    agent_error: str | None = None

    while not env.done and env.step_number < env.config.max_agent_steps:
        try:
            action = agent.next_action(instance, observation, history)
        except Exception as exc:
            agent_error = str(exc)
            _print_progress(
                f"[episode] agent={agent.name} scenario={instance.template.scenario_id} "
                f"seed={seed} agent_error={agent_error}"
            )
            break
        if not action:
            _print_progress(
                f"[episode] agent={agent.name} scenario={instance.template.scenario_id} "
                f"seed={seed} action=none"
            )
            break

        _print_progress(
            f"[step {env.step_number + 1}] action={action}"
        )
        transition = env.step(action)
        step = env.steps[-1]
        step_record = {
            "index": len(history) + 1,
            "command": step.command,
            "reward": step.reward,
            "return_code": step.return_code,
            "stdout": step.stdout,
            "stderr": step.stderr,
            "service_score": transition.service_score,
            "http_status": transition.observation.service_probe.http_status,
            "health_status": transition.observation.service_probe.health_status,
            "data_status": transition.observation.service_probe.data_status,
            "latency_ms": transition.observation.service_probe.latency_ms,
            "action_class": classify_action(step.command),
            "rejected": transition.info.get("rejected", False),
            "timed_out": transition.info.get("timed_out", False),
            "action_cost": transition.info.get("action_cost", 0.0),
        }
        history.append(step_record)
        _print_progress(
            f"[step {step_record['index']}] return_code={step.return_code} "
            f"reward={step.reward:.2f} score={transition.service_score:.2f} "
            f"health={step_record['health_status']} data={step_record['data_status']}"
        )
        observation = transition.observation
        if transition.done:
            break

    evaluation = env.evaluate(instance, env.steps)
    final_probe = probe_service(env.config)
    total_reward = round(sum(step.reward for step in env.steps), 3)
    _print_progress(
        f"[episode] agent={agent.name} scenario={instance.template.scenario_id} "
        f"seed={seed} finished verdict={evaluation.verdict.value} "
        f"oracle={evaluation.score:.2f} total_reward={total_reward:.2f}"
    )
    return {
        "agent": agent.name,
        "scenario_id": instance.template.scenario_id,
        "scenario_title": instance.template.title,
        "difficulty": instance.template.difficulty,
        "seed": seed,
        "chosen_parameters": instance.chosen_parameters,
        "recent_changes": instance.recent_changes,
        "initial_service_score": initial_observation.service_probe.score,
        "final_service_score": final_probe.score,
        "total_reward": total_reward,
        "steps": history,
        "step_count": len(history),
        "oracle": {
            "verdict": evaluation.verdict.value,
            "score": evaluation.score,
            "summary": evaluation.summary,
            "checks": [asdict(check) for check in evaluation.checks],
        },
        "agent_error": agent_error,
    }


def print_compact_summary(record: dict) -> None:
    print(
        "agent={agent} scenario={scenario} seed={seed} "
        "oracle={oracle:.2f} reward={reward:.2f} steps={steps}".format(
            agent=record["agent"],
            scenario=record["scenario_id"],
            seed=record["seed"],
            oracle=record["oracle"]["score"],
            reward=record["total_reward"],
            steps=record["step_count"],
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run tron benchmark agents across seeded scenarios.")
    parser.add_argument("--seeds", default=str(ROOT / "eval" / "seeds.yaml"))
    parser.add_argument("--agent", choices=["naive", "llm", "all"], default="all")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--seed", type=int, action="append", default=[])
    parser.add_argument("--output", default=str(ROOT / "eval" / "results.jsonl"))
    parser.add_argument("--max-agent-steps", type=int, default=12)
    parser.add_argument("--cluster-name", default="tron-eval")
    parser.add_argument("--ingress-port", type=int, default=8080)
    parser.add_argument("--hard-reset", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seed_plan = load_seed_plan(Path(args.seeds))
    selected_agents = ["naive", "llm"] if args.agent == "all" else [args.agent]
    scenario_filter = set(args.scenario)
    seed_override = args.seed

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", encoding="utf-8") as sink:
        for agent_name in selected_agents:
            agent = build_agent(agent_name)
            for scenario_entry in seed_plan:
                scenario_id = scenario_entry.get("id")
                if scenario_filter and scenario_id not in scenario_filter:
                    continue

                scenario_seeds = seed_override or [int(seed) for seed in scenario_entry.get("seeds", [])]
                for seed in scenario_seeds:
                    config = BenchmarkConfig(
                        random_seed=seed,
                        max_agent_steps=args.max_agent_steps,
                        work_dir=ROOT,
                        cluster=ClusterConfig(
                            cluster_name=args.cluster_name,
                            namespace="tron",
                            ingress_host="tron.localhost",
                            ingress_port=args.ingress_port,
                        ),
                    )
                    env = TronEnvironment(config)
                    record = run_episode(
                        env,
                        agent,
                        scenario_id=scenario_id,
                        seed=seed,
                        hard_reset=args.hard_reset,
                    )
                    print(f"Completed episode: agent={agent.name} scenario={record['scenario_id']} seed={seed}")
                    sink.write(json.dumps(record, default=_to_jsonable) + "\n")
                    print_compact_summary(record)


if __name__ == "__main__":
    main()
