from __future__ import annotations

"""Reward shaping helpers for the tron environment loop."""

from tron.action_analysis import command_family
from tron.models import AgentStep


DISCRIMINATING_BONUS_BY_FAMILY = {
    "app_config": 0.02,
    "redis_service": 0.02,
    "redis_endpoints": 0.02,
    "network_policy": 0.02,
    "nginx_deployment": 0.02,
    "bridge_logs": 0.02,
    "live_runtime_env": 0.03,
}


def discriminating_read_bonus(action: str, return_code: int, stdout: str) -> float:
    if return_code != 0:
        return 0.0
    family = command_family(action)
    if family == "app_config" and "REDIS_HOST:" not in stdout:
        return 0.0
    if family == "redis_service" and "selector:" not in stdout:
        return 0.0
    if family == "network_policy" and "kind: NetworkPolicy" not in stdout and "items:" not in stdout:
        return 0.0
    if family in {"redis_endpoints", "nginx_deployment", "bridge_logs", "live_runtime_env"} and not stdout.strip():
        return 0.0
    return DISCRIMINATING_BONUS_BY_FAMILY.get(family, 0.0)


def repeated_no_effect_penalty(
    action: str,
    new_service_score: float,
    previous_service_score: float,
    previous_steps: list[AgentStep],
) -> float:
    family = command_family(action)
    if family == "other" or new_service_score != previous_service_score:
        return 0.0

    recent_same_family = 0
    for previous in reversed(previous_steps):
        previous_family = command_family(previous.command)
        if previous_family != family or previous.reward > 0:
            break
        recent_same_family += 1

    if recent_same_family == 0:
        return 0.0
    if family in {"restart_nginx", "restart_redis"}:
        return -0.05 * min(recent_same_family, 3)
    if family in {"redis_service", "network_policy"}:
        return -0.05
    return 0.0
