from __future__ import annotations

"""Tool-using LLM baseline for the tron benchmark."""

from dataclasses import asdict, dataclass, field
import json
import os
import re
from textwrap import dedent
from typing import Protocol

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None

from models import ObservationBundle, ScenarioInstance

load_dotenv()


SYSTEM_PROMPT = dedent(
    """
    You are the control agent for the tron benchmark.

    Rules:
    - You may output exactly one command per turn.
    - The command must begin with kubectl or curl.
    - Do not output explanations, markdown, bullet points, or multiple commands.
    - All benchmark application resources are in namespace tron. Use kubectl -n tron for app diagnostics and repairs.
    - Do not inspect default or kube-system unless the observation explicitly points there. That is usually wasted effort here.
    - Diagnose before acting, but do not get stuck. After 2-3 non-improving diagnostic steps, choose the highest-probability repair in namespace tron.
    - Use the symptom pattern and cluster summaries to narrow the likely failure domain. Consider config drift, stale rollouts, service or endpoint mismatches, ingress routing, and namespace-scoped network policy.
    - Prefer durable repairs to temporary workarounds. Fix the drifted source of truth when possible, then verify the system is healthy.
    - Destructive actions cost points. Restarts, apply, delete, scale, and edit should be justified by evidence.
    - Step budget is limited. Use the current observation and history rather than repeating the same ineffective action.
    - The benchmark is partially observable. The black-box symptom is real, but the root cause is hidden.
    - Repeated commands that already returned no improvement are usually wrong. Change strategy.
    - Avoid interactive commands and broad wandering. Prefer commands that can complete non-interactively and move you toward either stronger evidence or a repair.
    - Use standalone kubectl or curl only. Do not use pipes, grep, jq, awk, sed, shell operators, or kubectl edit.
    - Do not use kubectl scale or direct ReplicaSet mutations. Those are benchmark shortcuts, not real repairs.
    - Prefer source objects that control runtime configuration, service wiring, routing, and policy. Do not patch mounted config blobs or embedded scripts unless the evidence specifically points there.
    - Do not repeat the same mutating command more than twice without improvement. If a repair attempt had no effect, gather a different high-value signal or change repair strategy.
    - A single restart is enough to test a stale-runtime hypothesis. If one restart does not help, inspect live pod env, service selectors, endpoints, ingress, or network policy before restarting again.
    - If source configuration already looks healthy but /data is still degraded, compare the live workload consumer against the source of truth. Check the running workload that serves /data before mutating another dependency.
    - When testing stale runtime, prefer reading live consumer state with jsonpath, describe, or exec. Do not alternate nginx and redis restarts without a new fact that points to the other workload.
    - Prefer robust pod-level reads over clever one-liners. First get the relevant pod name, then inspect that pod with `kubectl get pod <name> -o yaml`, `kubectl describe pod <name>`, or `kubectl exec <name> -- printenv REDIS_HOST`.
    - Avoid complex jsonpath filters over all pods. If a jsonpath read fails or returns nothing useful, fall back to a simpler pod-name read plus a direct pod inspection command.
    - For stale-runtime failures, the likely consumer is the workload serving /data. Inspect that workload's live env before restarting any dependency again.
    - Follow this loop strictly: classify symptom -> choose one hypothesis -> run one discriminating read -> apply one repair if confirmed -> re-probe -> if no improvement, switch hypothesis or assume a second fault.
    """
).strip()


class LLMClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass
class ActionProposal:
    intent: str
    command: str


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
            return json.dumps({"intent": "check current workload state", "command": "kubectl -n tron get pods"})
        command = self.commands[self.cursor % len(self.commands)]
        self.cursor += 1
        return json.dumps({"intent": "run the next fallback benchmark step", "command": command}, separators=(",", ":"))


@dataclass
class OpenAIChatClient:
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 30.0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if OpenAI is None:
            raise RuntimeError("openai SDK is required for OpenAI-backed llm runs")
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        response = client.chat.completions.create(
            model=self.model,
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


@dataclass
class OpenAIResponsesClient:
    model: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: float = 30.0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if OpenAI is None:
            raise RuntimeError("openai SDK is required for OpenAI-backed llm runs")
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        response = client.responses.create(
            model=self.model,
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


@dataclass
class AnthropicMessagesClient:
    model: str
    api_key: str
    base_url: str = "https://api.anthropic.com/v1"
    timeout_seconds: float = 30.0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if Anthropic is None:
            raise RuntimeError("anthropic SDK is required for Anthropic-backed llm runs")
        client = Anthropic(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )
        response = client.messages.create(
            model=self.model,
            max_tokens=128,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        fragments: list[str] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                fragments.append(block.text)
        return "".join(fragments).strip()


@dataclass
class LLMAgent:
    client: LLMClient
    name: str = "llm"
    last_intent: str | None = None

    def next_action(
        self,
        instance: ScenarioInstance,
        observation: ObservationBundle,
        history: list[dict],
    ) -> str:
        prompt = build_prompt(instance, observation, history)
        raw = self.client.complete(SYSTEM_PROMPT, prompt)
        proposal = parse_response(raw)
        self.last_intent = proposal.intent
        return proposal.command

    def describe_action(
        self,
        command: str,
        instance: ScenarioInstance,
        observation: ObservationBundle,
        history: list[dict],
    ) -> str:
        if self.last_intent:
            return self.last_intent
        del instance, history
        command_lower = command.lower()
        data_status = observation.service_probe.data_status
        health_status = observation.service_probe.health_status

        if "networkpolicy" in command_lower:
            if "delete" in command_lower or "patch" in command_lower:
                return "repairing namespace policy that may block backend traffic"
            return "checking namespace policy for blocked backend traffic"
        if "service redis" in command_lower or "endpoints" in command_lower:
            if "patch service redis" in command_lower:
                return "repairing service-to-pod wiring for redis"
            return "checking redis service wiring and backend endpoints"
        if "configmap app-config" in command_lower:
            if "patch configmap app-config" in command_lower:
                return "repairing source-of-truth application config"
            return "checking source-of-truth application config for drift"
        if "rollout restart" in command_lower:
            if health_status == "ok" and data_status == "error":
                return "testing stale-runtime hypothesis by refreshing pods"
            return "forcing a fresh rollout to apply a suspected fix"
        if "exec" in command_lower and "printenv" in command_lower:
            return "checking live workload environment against source configuration"
        if "deployment nginx" in command_lower or "deployment redis" in command_lower:
            if "set env" in command_lower or "set resources" in command_lower or "patch deployment" in command_lower:
                return "changing deployment runtime settings to test a repair"
            return "checking deployment state and live pod template"
        if "ingress" in command_lower:
            if "patch ingress" in command_lower:
                return "repairing external routing behavior"
            return "checking external routing configuration"
        if command_lower.startswith("curl "):
            return "probing external behavior to verify the current hypothesis"
        if "get pods" in command_lower or "describe pod" in command_lower:
            return "checking live workload state for evidence of the failure domain"
        return "gathering evidence or applying the most likely repair"


def observation_to_payload(
    instance: ScenarioInstance,
    observation: ObservationBundle,
    history: list[dict],
) -> dict:
    repeated_commands: dict[str, int] = {}
    repeated_families: dict[str, int] = {}
    non_improving_steps = 0
    observed_facts = summarize_observed_facts(history)
    ineffective_families = summarize_ineffective_families(history)
    recent_outcomes = summarize_recent_outcomes(history)

    for step in history:
        command = step.get("command", "")
        family = command_family(command)
        repeated_commands[command] = repeated_commands.get(command, 0) + 1
        repeated_families[family] = repeated_families.get(family, 0) + 1
        if step.get("reward", 0.0) <= 0:
            non_improving_steps += 1

    return {
        "scenario": {
            "id": instance.template.scenario_id,
            "title": instance.template.title,
            "difficulty": instance.template.difficulty,
        },
        "benchmark_context": {
            "target_namespace": "tron",
            "expected_pattern": "/health may stay ok while /data is degraded",
            "recommended_focus": [
                "deployment rollout state and pod readiness",
                "config and environment drift",
                "service selectors and endpoints",
                "ingress routing",
                "networkpolicy in tron",
            ],
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
        "history_summary": {
            "steps_taken": len(history),
            "non_improving_steps": non_improving_steps,
            "ineffective_families": ineffective_families,
            "repeated_commands": {
                command: count for command, count in repeated_commands.items() if count > 1
            },
            "repeated_families": {
                family: count for family, count in repeated_families.items() if count > 1
            },
        },
        "working_memory": {
            "observed_facts": observed_facts[-8:],
            "recent_outcomes": recent_outcomes,
        },
        "recent_history": history[-12:],
    }


def command_family(command: str) -> str:
    lowered = command.lower()
    if "configmap app-config" in lowered:
        return "app_config"
    if "networkpolicy" in lowered:
        return "network_policy"
    if "service redis" in lowered:
        return "redis_service"
    if "endpoints redis" in lowered:
        return "redis_endpoints"
    if "ingress" in lowered:
        return "ingress"
    if "rollout restart deployment/nginx" in lowered:
        return "restart_nginx"
    if "rollout restart deployment/redis" in lowered:
        return "restart_redis"
    if "deployment nginx" in lowered:
        return "nginx_deployment"
    if "logs" in lowered and "redis-bridge" in lowered:
        return "bridge_logs"
    if (
        ("get pods" in lowered or "get pod" in lowered or "exec " in lowered)
        and "redis_host" in lowered
    ):
        return "live_runtime_env"
    if "get pods" in lowered or "get pod" in lowered:
        return "pods"
    return "other"


def summarize_observed_facts(history: list[dict]) -> list[str]:
    facts: list[str] = []
    for step in history:
        command = step.get("command", "")
        stdout = step.get("stdout", "") or ""
        stderr = step.get("stderr", "") or ""
        family = command_family(command)
        if not stdout and not stderr:
            continue
        snippets: list[str] = []
        primary = stdout.strip() or stderr.strip()
        if family == "live_runtime_env" and primary:
            snippets.append(f"live runtime value observed: {primary.splitlines()[0][:80]}")
        elif family == "bridge_logs" and primary:
            snippets.append(f"log signal observed: {primary.splitlines()[0][:80]}")
        elif family in {"app_config", "redis_service", "redis_endpoints", "network_policy", "nginx_deployment", "pods", "ingress"}:
            compact = " ".join(primary.split())
            if compact:
                snippets.append(f"{family} observation: {compact[:120]}")
        elif primary:
            snippets.append(f"{family} output observed: {' '.join(primary.split())[:120]}")
        for snippet in snippets:
            facts.append(snippet)
    return list(dict.fromkeys(facts))


def summarize_ineffective_families(history: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in history:
        family = command_family(step.get("command", ""))
        if family == "other":
            continue
        if step.get("reward", 0.0) <= 0 and step.get("action_class") == "destructive":
            counts[family] = counts.get(family, 0) + 1
    return counts


def summarize_recent_outcomes(history: list[dict]) -> list[dict[str, object]]:
    outcomes: list[dict[str, object]] = []
    for step in history[-6:]:
        outcomes.append(
            {
                "family": command_family(step.get("command", "")),
                "reward": step.get("reward", 0.0),
                "service_score": step.get("service_score"),
                "return_code": step.get("return_code"),
                "action_class": step.get("action_class"),
            }
        )
    return outcomes


def build_prompt(
    instance: ScenarioInstance,
    observation: ObservationBundle,
    history: list[dict] | None = None,
) -> str:
    payload = observation_to_payload(instance, observation, history or [])
    return dedent(
        f"""
        Return exactly one kubectl or curl command for the next turn.
        Return exactly one single-line JSON object with keys "intent" and "command".
        Keep "intent" under 12 words and make it a plain one-line phrase.
        Stay in namespace tron unless there is strong contrary evidence.
        If there have already been several non-improving diagnostic steps, prefer a repair action over another generic get/describe.
        Do not tailor to a single canned incident. Use the observation, recent-change hint, and recent history to choose the next highest-value command.
        Aim for a real fix, not just a temporary workaround. Prefer correcting source objects such as ConfigMaps, Services, Ingresses, NetworkPolicies, and rollout state before patching runtime overrides.
        Use kubectl's built-in output options like -o yaml, -o json, or -o jsonpath rather than shell pipelines or jq.
        Prefer robust pod-level reads over clever one-liners. First get the relevant pod name, then inspect that pod with get/describe/exec.
        Avoid complex jsonpath filters over all pods. If a jsonpath read fails or returns nothing useful, fall back to a simpler pod-name read plus direct pod inspection.
        Treat mounted config blobs and embedded scripts as lower priority unless the evidence specifically points to route handling or file-based config drift.
        If the visible config already looks healthy but the service is still degraded, prefer checking live pod env, service selectors, and endpoints before more restarts.
        If one restart did not help and the same consumer still serves /data, do not restart it again until you have one new fact from that consumer's live state.
        Example format: {{"intent":"check live consumer env","command":"kubectl -n tron exec nginx-abc -- printenv REDIS_HOST"}}

        Current state:
        {json.dumps(payload, indent=2, sort_keys=True)}
        """
    ).strip()


def parse_response(raw_text: str) -> ActionProposal:
    cleaned = raw_text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:-1]).strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("LLM output must contain exactly one non-empty line")
    line = lines[0]
    if line.startswith("{"):
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("LLM output JSON must be an object")
        intent = str(payload.get("intent", "")).strip()
        command = str(payload.get("command", "")).strip()
        if not intent:
            raise ValueError("LLM output JSON must include a non-empty intent")
        if "\n" in intent:
            raise ValueError("LLM intent must be one line")
        if len(intent.split()) > 12:
            raise ValueError("LLM intent must be concise")
    else:
        intent = "execute next benchmark action"
        command = line
    if not (command.startswith("kubectl ") or command.startswith("curl ")):
        raise ValueError("LLM output must start with kubectl or curl")
    return ActionProposal(intent=intent, command=command)


def parse_command(raw_text: str) -> str:
    return parse_response(raw_text).command


def build_client_from_env() -> LLMClient:
    static_plan = os.getenv("TRON_LLM_PLAN", "").strip()
    if static_plan:
        return StaticPlanClient(commands=[line.strip() for line in static_plan.splitlines() if line.strip()])

    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-5-mini")
    openai_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_api_mode = os.getenv("OPENAI_API_MODE", "").strip().lower()
    if openai_api_key:
        if openai_api_mode == "responses" or openai_model.startswith("gpt-5"):
            return OpenAIResponsesClient(
                model=openai_model,
                api_key=openai_api_key,
                base_url=openai_base_url,
            )
        return OpenAIChatClient(
            model=openai_model,
            api_key=openai_api_key,
            base_url=openai_base_url,
        )

    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
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
