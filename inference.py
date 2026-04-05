from __future__ import annotations

"""Baseline inference runner for the tron OpenEnv wrapper."""

import argparse
import json
import os
from typing import Any, Protocol

from baseline.llm_agent import ActionProposal, parse_response

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

from tron_openenv.client import TronEnvClient
from tron_openenv.models import TronObservation, TronState, TronTask
from tron_openenv.server.app import create_app

load_dotenv()


TASK_ORDER = ("easy", "medium", "hard")
SYSTEM_PROMPT = """
You are the control agent for the tron OpenEnv benchmark.

Rules:
- Output exactly one single-line JSON object with keys "intent" and "command".
- The command must start with kubectl or curl.
- Use kubectl -n tron for application diagnostics and repairs.
- Stay focused on the current incident. Prefer one discriminating read, then one repair, then re-probe.
- Aim for durable source-of-truth repairs, not temporary overrides.
- Avoid interactive commands, shell operators, jq, grep, awk, sed, kubectl edit, kubectl scale, and direct ReplicaSet mutation.
- Keep intent concise.
""".strip()


class SupportsCompletion(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        ...


class OpenAIPlanner:
    def __init__(self, api_base_url: str, model_name: str, api_key: str) -> None:
        if OpenAI is None:
            raise RuntimeError("openai package is required to run inference.py")
        self.client = OpenAI(base_url=api_base_url, api_key=api_key, timeout=60.0)
        self.model_name = model_name

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self.model_name.startswith("gpt-5"):
            response = self.client.responses.create(
                model=self.model_name,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            output_text = getattr(response, "output_text", "")
            if isinstance(output_text, str) and output_text.strip():
                return output_text.strip()
            fragments: list[str] = []
            for item in getattr(response, "output", []) or []:
                for content in getattr(item, "content", []) or []:
                    if getattr(content, "type", "") == "output_text":
                        fragments.append(getattr(content, "text", ""))
            return "".join(fragments).strip()

        response = self.client.chat.completions.create(
            model=self.model_name,
            temperature=0,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if isinstance(content, str):
            return content.strip()
        return "".join(part.text for part in content or [] if getattr(part, "type", "") == "text").strip()


def emit(tag: str, payload: dict) -> None:
    print(f"[{tag}] {json.dumps(payload, separators=(',', ':'), ensure_ascii=True)}", flush=True)


def build_prompt(task: TronTask, observation: TronObservation, history: list[dict]) -> str:
    payload = {
        "task": task.model_dump(),
        "observation": observation.model_dump(),
        "recent_history": history[-6:],
    }
    return (
        "Return the next highest-value benchmark command.\n"
        "Return exactly one single-line JSON object with keys intent and command.\n\n"
        f"Current state:\n{json.dumps(payload, indent=2, sort_keys=True)}"
    )


def build_env_client(base_url: str | None) -> TronEnvClient:
    if base_url:
        return TronEnvClient(base_url=base_url)
    if TestClient is None:
        raise RuntimeError("fastapi[testclient] support is required for local inference mode")
    return TronEnvClient(base_url="http://testserver", session=TestClient(create_app()))


def _strip_code_fences(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        return "\n".join(cleaned.splitlines()[1:-1]).strip()
    return cleaned


def _coerce_intent(intent: str) -> str:
    normalized = " ".join(intent.split())
    if not normalized:
        return "execute next benchmark action"
    return " ".join(normalized.split()[:12])


def _first_command_line(text: str) -> str:
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("kubectl ") or candidate.startswith("curl "):
            return candidate
    raise ValueError("LLM output must include a kubectl or curl command")


def parse_planner_response(raw_text: str) -> ActionProposal:
    try:
        return parse_response(raw_text)
    except ValueError:
        cleaned = _strip_code_fences(raw_text)
        if not cleaned:
            raise
        if cleaned.startswith("{"):
            payload: dict[str, Any] = json.loads(cleaned)
            if not isinstance(payload, dict):
                raise ValueError("LLM output JSON must be an object")
            intent = _coerce_intent(str(payload.get("intent", "")).strip())
            command = str(payload.get("command", "")).strip() or _first_command_line(cleaned)
        else:
            intent = "execute next benchmark action"
            command = _first_command_line(cleaned)
        if not (command.startswith("kubectl ") or command.startswith("curl ")):
            raise ValueError("LLM output must start with kubectl or curl")
        return ActionProposal(intent=intent, command=command)


def run_task(
    env_client: TronEnvClient,
    planner: SupportsCompletion,
    task_id: str,
    seed: int,
    hard_reset: bool = False,
) -> dict:
    reset_result = env_client.reset(task_id=task_id, seed=seed, hard_reset=hard_reset)
    task = reset_result.task
    observation = reset_result.observation
    history: list[dict] = []

    emit(
        "START",
        {
            "task_id": task.id,
            "scenario_id": task.scenario_id,
            "seed": seed,
            "difficulty": task.difficulty,
            "max_agent_steps": task.max_agent_steps,
        },
    )

    while not observation.done and observation.step_count < task.max_agent_steps:
        raw = planner.complete(SYSTEM_PROMPT, build_prompt(task, observation, history))
        proposal = parse_planner_response(raw)
        step_result = env_client.step(proposal.command)
        observation = step_result.observation
        step_payload = {
            "task_id": task.id,
            "step": observation.step_count,
            "intent": proposal.intent,
            "command": proposal.command,
            "reward": step_result.reward.value,
            "service_score": observation.service_probe.score,
            "done": step_result.done,
        }
        if "oracle_score" in step_result.info:
            step_payload["oracle_score"] = step_result.info["oracle_score"]
        emit("STEP", step_payload)
        history.append(
            {
                "step": observation.step_count,
                "command": proposal.command,
                "intent": proposal.intent,
                "reward": step_result.reward.value,
                "service_score": observation.service_probe.score,
                "return_code": step_result.info.get("return_code"),
                "done": step_result.done,
            }
        )
        if step_result.done:
            break

    final_state: TronState = env_client.state()
    final_payload = {
        "task_id": task.id,
        "scenario_id": task.scenario_id,
        "seed": seed,
        "steps": final_state.step_count,
        "cumulative_reward": final_state.cumulative_reward,
        "service_score": final_state.service_score,
        "oracle_score": final_state.oracle_score,
        "oracle_verdict": final_state.oracle_verdict,
    }
    emit("END", final_payload)
    return final_payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tron OpenEnv baseline inference loop.")
    parser.add_argument("--env-base-url", default=os.getenv("ENV_BASE_URL", "").strip())
    parser.add_argument("--hard-reset", action="store_true")
    parser.add_argument("--task", action="append", choices=list(TASK_ORDER), default=[])
    parser.add_argument("--seed", type=int, default=11)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    api_base_url = os.getenv("API_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    model_name = os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL")
    hf_token = os.getenv("HF_TOKEN") or os.getenv("OPENAI_API_KEY")
    if not api_base_url or not model_name or not hf_token:
        raise RuntimeError(
            "Set API_BASE_URL, MODEL_NAME, and HF_TOKEN "
            "(or OPENAI_BASE_URL, OPENAI_MODEL, and OPENAI_API_KEY) before running inference.py"
        )

    planner = OpenAIPlanner(api_base_url=api_base_url, model_name=model_name, api_key=hf_token)
    env_client = build_env_client(args.env_base_url or None)

    try:
        task_ids = args.task or list(TASK_ORDER)
        for task_id in task_ids:
            run_task(
                env_client=env_client,
                planner=planner,
                task_id=task_id,
                seed=args.seed,
                hard_reset=args.hard_reset,
            )
    finally:
        env_client.close()


if __name__ == "__main__":
    main()
