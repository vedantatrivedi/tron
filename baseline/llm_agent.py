from __future__ import annotations

"""Tool-using LLM baseline for the tron benchmark."""

from dataclasses import asdict, dataclass, field
import json
import os
from textwrap import dedent
from typing import Protocol

import requests

from models import ObservationBundle, ScenarioInstance


SYSTEM_PROMPT = dedent(
    """
    You are the control agent for the tron benchmark.

    Rules:
    - You may output exactly one command per turn.
    - The command must begin with kubectl or curl.
    - Do not output explanations, markdown, bullet points, or multiple commands.
    - Diagnose before acting. Prefer kubectl get, describe, logs, top, rollout history, exec, or curl first.
    - Destructive actions cost points. Restarts, apply, delete, scale, and edit should be justified by evidence.
    - Step budget is limited. Use the current observation rather than repeating the same ineffective action.
    - The benchmark is partially observable. The black-box symptom is real, but the root cause is hidden.
    """
).strip()


class LLMClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass
class StaticPlanClient:
    """Deterministic fallback used for tests and offline runs."""

    commands: list[str] = field(
        default_factory=lambda: [
            "kubectl -n tron get pods",
            "kubectl -n tron get configmap app-config -o yaml",
            "kubectl -n tron get service redis -o yaml",
            "kubectl -n tron get ingress tron-ingress -o yaml",
            "curl -sS -H 'Host: tron.localhost' http://127.0.0.1:8080/data",
        ]
    )
    cursor: int = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        del system_prompt, user_prompt
        if not self.commands:
            return "kubectl -n tron get pods"
        command = self.commands[self.cursor % len(self.commands)]
        self.cursor += 1
        return command


@dataclass
class OpenAIChatClient:
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 30.0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()


@dataclass
class AnthropicMessagesClient:
    model: str
    api_key: str
    base_url: str = "https://api.anthropic.com/v1"
    timeout_seconds: float = 30.0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = requests.post(
            f"{self.base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 128,
                "temperature": 0,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return payload["content"][0]["text"].strip()


@dataclass
class LLMAgent:
    client: LLMClient
    name: str = "llm"

    def next_action(
        self,
        instance: ScenarioInstance,
        observation: ObservationBundle,
        history: list[dict],
    ) -> str:
        prompt = build_prompt(instance, observation, history)
        raw = self.client.complete(SYSTEM_PROMPT, prompt)
        return parse_command(raw)


def observation_to_payload(
    instance: ScenarioInstance,
    observation: ObservationBundle,
    history: list[dict],
) -> dict:
    return {
        "scenario": {
            "id": instance.template.scenario_id,
            "title": instance.template.title,
            "difficulty": instance.template.difficulty,
        },
        "observation": {
            "incident_brief": observation.incident_brief,
            "step_number": observation.step_number,
            "last_action": observation.last_action,
            "last_reward": observation.last_reward,
            "service_probe": asdict(observation.service_probe),
            "cluster_summary": asdict(observation.cluster_summary),
            "recent_change_hint": observation.recent_change_hint,
        },
        "recent_history": history[-4:],
    }


def build_prompt(
    instance: ScenarioInstance,
    observation: ObservationBundle,
    history: list[dict] | None = None,
) -> str:
    payload = observation_to_payload(instance, observation, history or [])
    return dedent(
        f"""
        Return exactly one kubectl or curl command for the next turn.

        Current state:
        {json.dumps(payload, indent=2, sort_keys=True)}
        """
    ).strip()


def parse_command(raw_text: str) -> str:
    cleaned = raw_text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("LLM output must contain exactly one non-empty line")
    command = lines[0]
    if not (command.startswith("kubectl ") or command.startswith("curl ")):
        raise ValueError("LLM output must start with kubectl or curl")
    return command


def build_client_from_env() -> LLMClient:
    static_plan = os.getenv("TRON_LLM_PLAN", "").strip()
    if static_plan:
        return StaticPlanClient(commands=[line.strip() for line in static_plan.splitlines() if line.strip()])

    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if openai_api_key:
        return OpenAIChatClient(
            model=openai_model,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
    anthropic_base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    if anthropic_api_key:
        return AnthropicMessagesClient(
            model=anthropic_model,
            api_key=anthropic_api_key,
            base_url=anthropic_base_url,
        )

    return StaticPlanClient()


def build_agent(client: LLMClient | None = None) -> LLMAgent:
    return LLMAgent(client=client or build_client_from_env())


def plan_actions(instance: ScenarioInstance, observations: ObservationBundle) -> list[str]:
    agent = build_agent()
    return [agent.next_action(instance, observations, history=[])]
