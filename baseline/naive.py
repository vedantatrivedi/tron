from __future__ import annotations

"""Weak brute-force baseline for the tron benchmark."""

from dataclasses import dataclass, field

from models import ObservationBundle, ScenarioInstance


PLAYBOOK = [
    "kubectl -n tron get pods",
    "kubectl -n tron rollout restart deployment/nginx",
    "kubectl -n tron rollout restart deployment/redis",
    "kubectl apply -f manifests/configmap.yaml",
    "kubectl apply -f manifests/redis.yaml",
    "kubectl apply -f manifests/nginx.yaml",
    "kubectl apply -f manifests/ingress.yaml",
    "kubectl -n tron delete pod -l app=nginx",
]


@dataclass
class NaiveAgent:
    """Cycle through an intentionally expensive recovery playbook."""

    playbook: list[str] = field(default_factory=lambda: PLAYBOOK.copy())
    cursor: int = 0
    name: str = "naive"

    def next_action(
        self,
        instance: ScenarioInstance,
        observation: ObservationBundle,
        history: list[dict],
    ) -> str | None:
        del instance, observation, history
        if not self.playbook:
            return None
        action = self.playbook[self.cursor % len(self.playbook)]
        self.cursor += 1
        return action


def build_agent() -> NaiveAgent:
    return NaiveAgent()


def plan_actions(instance: ScenarioInstance, observations: ObservationBundle) -> list[str]:
    del instance, observations
    return PLAYBOOK.copy()
