from __future__ import annotations

"""Shared helpers and restore commands for scenario definitions."""

from tron.models import RepairCheck


DATA_URL = "http://127.0.0.1:8080/data"
BASE_CONFIGMAP_RESTORE = "kubectl apply -f manifests/configmap.yaml"
BASE_NGINX_RESTORE = (
    "kubectl apply -f manifests/nginx.yaml && "
    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
)
BASE_REDIS_RESTORE = (
    "kubectl apply -f manifests/redis.yaml && "
    "kubectl -n tron rollout status deployment/redis --timeout=120s"
)
BASE_INGRESS_RESTORE = "kubectl apply -f manifests/ingress.yaml"
BASE_NETWORKPOLICY_RESTORE = "kubectl apply -f manifests/networkpolicy-base.yaml"
RESTART_NGINX = (
    "kubectl -n tron rollout restart deployment/nginx && "
    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
)


def equals(name: str, command: list[str], expected: str) -> RepairCheck:
    return RepairCheck(name=name, command=command, success_substring=expected, match_mode="equals")


def contains(name: str, command: list[str], expected: str) -> RepairCheck:
    return RepairCheck(name=name, command=command, success_substring=expected, match_mode="contains")


def shell_equals(name: str, script: str, expected: str) -> RepairCheck:
    return equals(name, ["sh", "-lc", script], expected)
