from __future__ import annotations

"""Shared helpers for classifying benchmark commands."""


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


def classify_action(command: str) -> str:
    stripped = command.strip()
    diagnostic_prefixes = (
        "curl ",
        "kubectl get ",
        "kubectl describe ",
        "kubectl logs ",
        "kubectl top ",
        "kubectl exec ",
    )
    if stripped.startswith(diagnostic_prefixes):
        return "diagnostic"
    if " rollout history " in stripped or " rollout status " in stripped:
        return "diagnostic"
    return "destructive"
