from __future__ import annotations

"""Tiered default observations for the tron runtime."""

import json

from tron.executor import CommandExecutor
from tron.models import BenchmarkConfig, ClusterSummary, ObservationBundle, ScenarioInstance, ServiceProbe


def _compact_lines(text: str, limit: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "none"
    if len(lines) <= limit:
        return "; ".join(lines)
    return "; ".join(lines[:limit]) + "; ...[truncated]"


def _summarize_pod(item: dict) -> str:
    metadata = item.get("metadata", {})
    status = item.get("status", {})
    spec = item.get("spec", {})
    name = metadata.get("name", "unknown")
    container_count = len(spec.get("containers", []))
    ready_count = sum(
        1 for state in status.get("containerStatuses", []) if state.get("ready")
    )
    return f"{name} {ready_count}/{container_count} {status.get('phase', 'Unknown')}"


def _summarize_service(item: dict) -> str:
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    return f"{metadata.get('name', 'unknown')} {spec.get('type', 'ClusterIP')}"


def _summarize_deployment(item: dict) -> str:
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    status = item.get("status", {})
    return (
        f"{metadata.get('name', 'unknown')} "
        f"{status.get('availableReplicas', 0)}/{spec.get('replicas', 0)}"
    )


def _summarize_endpoints(item: dict) -> str:
    metadata = item.get("metadata", {})
    subsets = item.get("subsets") or []
    rendered: list[str] = []
    for subset in subsets:
        ports = [port.get("port") for port in subset.get("ports", []) if port.get("port") is not None]
        for address in subset.get("addresses", []):
            ip = address.get("ip", "?")
            if ports:
                rendered.extend(f"{ip}:{port}" for port in ports)
            else:
                rendered.append(ip)
    suffix = ", ".join(rendered) if rendered else "<none>"
    return f"{metadata.get('name', 'unknown')} {suffix}"


def _get_cluster_summary(executor: CommandExecutor, namespace: str, limit: int) -> ClusterSummary:
    result = executor.run(f"kubectl -n {namespace} get pods,services,deployments,endpoints -o json")
    payload_text = result.stdout or result.stderr
    if result.return_code != 0:
        fallback = _compact_lines(payload_text, limit)
        return ClusterSummary(
            pods=fallback,
            services=fallback,
            deployments=fallback,
            endpoints=fallback,
        )

    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        fallback = _compact_lines(payload_text, limit)
        return ClusterSummary(
            pods=fallback,
            services=fallback,
            deployments=fallback,
            endpoints=fallback,
        )
    buckets: dict[str, list[str]] = {
        "Pod": [],
        "Service": [],
        "Deployment": [],
        "Endpoints": [],
    }
    for item in payload.get("items", []):
        kind = item.get("kind")
        if kind == "Pod":
            buckets["Pod"].append(_summarize_pod(item))
        elif kind == "Service":
            buckets["Service"].append(_summarize_service(item))
        elif kind == "Deployment":
            buckets["Deployment"].append(_summarize_deployment(item))
        elif kind == "Endpoints":
            buckets["Endpoints"].append(_summarize_endpoints(item))

    return ClusterSummary(
        pods=_compact_lines("\n".join(buckets["Pod"]), limit),
        services=_compact_lines("\n".join(buckets["Service"]), limit),
        deployments=_compact_lines("\n".join(buckets["Deployment"]), limit),
        endpoints=_compact_lines("\n".join(buckets["Endpoints"]), limit),
    )


def _placeholder_cluster_summary(reason: str) -> ClusterSummary:
    return ClusterSummary(
        pods=reason,
        services=reason,
        deployments=reason,
        endpoints=reason,
    )


def collect_observations(
    executor: CommandExecutor,
    config: BenchmarkConfig,
    instance: ScenarioInstance,
    step_number: int,
    last_action: str | None,
    last_reward: float,
    service_probe: ServiceProbe,
    include_cluster_summary: bool = True,
) -> ObservationBundle:
    """Collect the default low-cost observation bundle."""

    namespace = config.cluster.namespace
    if include_cluster_summary:
        cluster_summary = _get_cluster_summary(executor, namespace, config.observation_line_limit)
    else:
        cluster_summary = _placeholder_cluster_summary("omitted during fast reset")

    recent_hint = "no recent change hint"
    if instance.recent_changes:
        recent_hint = instance.recent_changes[min(2, len(instance.recent_changes) - 1)]

    return ObservationBundle(
        incident_brief=instance.template.user_visible_symptom,
        step_number=step_number,
        last_action=last_action,
        last_reward=last_reward,
        service_probe=service_probe,
        cluster_summary=cluster_summary,
        recent_change_hint=recent_hint,
    )
