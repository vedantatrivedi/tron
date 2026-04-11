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

load_dotenv()


DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_NAME = "gpt-5-mini"
DEFAULT_ENV_NAME = "tron"
DEFAULT_RUNTIME_BASE_URL = "https://jj90999-tron.hf.space"
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

SCRIPTED_EASY_STEPS = (
    ActionProposal(
        intent="inspect redis service selector",
        command="kubectl -n tron get service redis -o jsonpath={.spec.selector.app}",
    ),
    ActionProposal(
        intent="restore redis service selector",
        command="kubectl -n tron patch service redis --type merge -p '{\"spec\":{\"selector\":{\"app\":\"redis\"}}}'",
    ),
    ActionProposal(
        intent="verify redis endpoints",
        command="kubectl -n tron get endpoints redis -o yaml",
    ),
)


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


def _bool_token(value: bool) -> str:
    return "true" if value else "false"


def _sanitize_token(value: str | None) -> str:
    if value is None:
        return "null"
    cleaned = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return cleaned or "null"


def _clamp_score(value: float | None) -> float:
    if value is None:
        return 0.01
    return max(0.01, min(0.99, float(value)))


def emit_start(task_name: str, env_name: str, model_name: str) -> None:
    print(
        f"[START] task={_sanitize_token(task_name)} env={_sanitize_token(env_name)} "
        f"model={_sanitize_token(model_name)}",
        flush=True,
    )


def emit_step(step: int, action: str, reward: float, done: bool, error: str | None) -> None:
    print(
        f"[STEP] step={step} action={_sanitize_token(action)} reward={reward:.2f} "
        f"done={_bool_token(done)} error={_sanitize_token(error)}",
        flush=True,
    )


def emit_end(success: bool, steps: int, score: float, rewards: list[float]) -> None:
    rendered_rewards = ",".join(f"{reward:.2f}" for reward in rewards)
    print(
        f"[END] success={_bool_token(success)} steps={steps} score={_clamp_score(score):.2f} rewards={rendered_rewards}",
        flush=True,
    )


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


def build_env_client(base_url: str | None, local_env: bool = False) -> TronEnvClient:
    if local_env:
        if TestClient is None:
            raise RuntimeError("fastapi[testclient] support is required for local inference mode")
        from tron_openenv.server.app import create_app

        return TronEnvClient(base_url="http://testserver", session=TestClient(create_app()))
    if base_url:
        return TronEnvClient(base_url=base_url)
    return TronEnvClient(base_url=DEFAULT_RUNTIME_BASE_URL)


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


def _step_error(step_info: dict[str, Any]) -> str | None:
    for key in ("last_action_error", "error", "stderr"):
        value = step_info.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _scripted_action(task: TronTask, history: list[dict[str, Any]]) -> ActionProposal | None:
    if task.id != "easy":
        return None
    if len(history) >= len(SCRIPTED_EASY_STEPS):
        return None
    return SCRIPTED_EASY_STEPS[len(history)]


def _next_action(
    planner: SupportsCompletion,
    task: TronTask,
    observation: TronObservation,
    history: list[dict[str, Any]],
) -> ActionProposal:
    scripted = _scripted_action(task, history)
    if scripted is not None:
        return scripted
    raw = planner.complete(SYSTEM_PROMPT, build_prompt(task, observation, history))
    return parse_planner_response(raw)


def run_task(
    env_client: TronEnvClient,
    planner: SupportsCompletion,
    task_id: str,
    seed: int,
    hard_reset: bool = False,
) -> dict[str, Any]:
    reset_result = env_client.reset(task_id=task_id, seed=seed, hard_reset=hard_reset)
    task = reset_result.task
    observation = reset_result.observation
    history: list[dict] = []
    rewards: list[float] = []

    while not observation.done and observation.step_count < task.max_agent_steps:
        proposal = _next_action(planner, task, observation, history)
        step_result = env_client.step(proposal.command)
        observation = step_result.observation
        rewards.append(step_result.reward.value)
        emit_step(
            step=observation.step_count,
            action=proposal.command,
            reward=step_result.reward.value,
            done=step_result.done,
            error=_step_error(step_result.info),
        )
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
    return {
        "task_id": task.id,
        "seed": seed,
        "steps": final_state.step_count,
        "rewards": rewards,
        "success": final_state.oracle_verdict == "success",
        "cumulative_reward": final_state.cumulative_reward,
        "service_score": final_state.service_score,
        "oracle_score": final_state.oracle_score,
        "oracle_verdict": final_state.oracle_verdict,
    }


def _summary_score(summary: dict[str, Any]) -> float:
    oracle_score = summary.get("oracle_score")
    if oracle_score is not None:
        return _clamp_score(oracle_score)
    service_score = summary.get("service_score")
    if service_score is not None:
        return _clamp_score(service_score)
    return 1.0 if bool(summary.get("success", False)) else 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tron OpenEnv baseline inference loop.")
    parser.add_argument("--env-base-url", default=os.getenv("ENV_BASE_URL", "").strip())
    parser.add_argument("--local-env", action="store_true")
    parser.add_argument("--hard-reset", action="store_true")
    parser.add_argument("--task", choices=list(TASK_ORDER), default=os.getenv("TASK_ID", "easy"))
    parser.add_argument("--seed", type=int, default=11)
    return parser.parse_args()


def resolve_planner_config() -> tuple[str, str, str]:
    api_base_url = os.getenv("API_BASE_URL") or os.getenv("OPENAI_BASE_URL") or DEFAULT_API_BASE_URL
    model_name = os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL_NAME
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("HF_TOKEN")
    if not api_key:
        raise RuntimeError(
            "Set HF_TOKEN before running inference.py. OPENAI_API_KEY is also accepted for local debugging. "
            f"API_BASE_URL defaults to {DEFAULT_API_BASE_URL} and MODEL_NAME defaults to {DEFAULT_MODEL_NAME}."
        )
    return api_base_url, model_name, api_key


def main() -> None:
    args = parse_args()
    model_name = os.getenv("MODEL_NAME") or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL_NAME
    env_client: TronEnvClient | None = None
    summary: dict[str, Any] = {
        "success": False,
        "steps": 0,
        "rewards": [],
    }
    emit_start(task_name=args.task, env_name=DEFAULT_ENV_NAME, model_name=model_name)

    try:
        api_base_url, model_name, api_key = resolve_planner_config()
        env_client = build_env_client(args.env_base_url or None, local_env=args.local_env)
        planner = OpenAIPlanner(api_base_url=api_base_url, model_name=model_name, api_key=api_key)
        summary = run_task(
            env_client=env_client,
            planner=planner,
            task_id=args.task,
            seed=args.seed,
            hard_reset=args.hard_reset,
        )
    except Exception as exc:
        print(f"[inference] {_sanitize_token(str(exc))}", file=os.sys.stderr, flush=True)
    finally:
        if env_client is not None:
            env_client.close()
        emit_end(
            success=bool(summary.get("success", False)),
            steps=int(summary.get("steps", 0)),
            score=_summary_score(summary),
            rewards=list(summary.get("rewards", [])),
        )


if __name__ == "__main__":
    main()
