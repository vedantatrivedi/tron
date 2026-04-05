from __future__ import annotations

"""Shared helpers for cluster bootstrap and baseline restore commands."""

from typing import Protocol

from tron.models import ClusterConfig


class SupportsTrustedExecution(Protocol):
    def run(self, command: str, timeout: float = 20.0): ...


def build_cluster_env_prefix(cluster: ClusterConfig) -> str:
    return (
        f"CLUSTER_NAME={cluster.cluster_name} "
        f"INGRESS_HOST={cluster.ingress_host} "
        f"INGRESS_PORT={cluster.ingress_port} "
        f"NAMESPACE={cluster.namespace}"
    )


def build_hard_reset_commands(cluster: ClusterConfig) -> list[str]:
    prefix = build_cluster_env_prefix(cluster)
    return [f"{prefix} bash ./cleanup.sh", f"{prefix} bash ./setup.sh"]


def build_baseline_restore_commands(namespace: str) -> list[str]:
    return [
        "kubectl apply --validate=false -f manifests/namespace.yaml",
        f"kubectl -n {namespace} apply --validate=false -f manifests/configmap.yaml",
        f"kubectl -n {namespace} apply --validate=false -f manifests/redis.yaml",
        f"kubectl -n {namespace} apply --validate=false -f manifests/nginx.yaml",
        f"kubectl -n {namespace} apply --validate=false -f manifests/ingress.yaml",
        f"kubectl -n {namespace} apply --validate=false -f manifests/networkpolicy-base.yaml",
        f"kubectl -n {namespace} set env deployment/nginx REDIS_HOST-",
        f"kubectl -n {namespace} rollout status deployment/redis --timeout=120s",
        f"kubectl -n {namespace} rollout status deployment/nginx --timeout=120s",
    ]


def run_checked_commands(
    executor: SupportsTrustedExecution,
    commands: list[str],
    timeout: float,
    stage: str,
) -> None:
    for command in commands:
        result = executor.run(command, timeout=timeout)
        if result.return_code != 0:
            details = result.stderr or result.stdout or "command failed with no output"
            raise RuntimeError(f"{stage} failed for `{command}`: {details}")
