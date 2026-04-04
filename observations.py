from __future__ import annotations

"""Tiered default observations for the tron runtime."""

from executor import CommandExecutor
from models import BenchmarkConfig, ClusterSummary, ObservationBundle, ScenarioInstance, ServiceProbe


def _compact_lines(text: str, limit: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "none"
    if len(lines) <= limit:
        return "; ".join(lines)
    return "; ".join(lines[:limit]) + "; ...[truncated]"


def _get_resource_summary(
    executor: CommandExecutor,
    command: str,
    limit: int,
) -> str:
    result = executor.run(command)
    return _compact_lines(result.stdout or result.stderr, limit)


def collect_observations(
    executor: CommandExecutor,
    config: BenchmarkConfig,
    instance: ScenarioInstance,
    step_number: int,
    last_action: str | None,
    last_reward: float,
    service_probe: ServiceProbe,
) -> ObservationBundle:
    """Collect the default low-cost observation bundle."""

    namespace = config.cluster.namespace
    cluster_summary = ClusterSummary(
        pods=_get_resource_summary(
            executor,
            f"kubectl -n {namespace} get pods --no-headers",
            config.observation_line_limit,
        ),
        services=_get_resource_summary(
            executor,
            f"kubectl -n {namespace} get services --no-headers",
            config.observation_line_limit,
        ),
        deployments=_get_resource_summary(
            executor,
            f"kubectl -n {namespace} get deployments --no-headers",
            config.observation_line_limit,
        ),
        endpoints=_get_resource_summary(
            executor,
            f"kubectl -n {namespace} get endpoints --no-headers",
            config.observation_line_limit,
        ),
    )

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
