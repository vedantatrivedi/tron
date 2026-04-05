from __future__ import annotations

"""Resource pressure scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import (
    BASE_CONFIGMAP_RESTORE,
    BASE_NGINX_RESTORE,
    equals,
    shell_equals,
)


def build_resource_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="cpu-limits-too-low",
            kind=ScenarioKind.RESOURCE,
            title="CPU limits are too low for the current traffic profile",
            trigger_context=(
                "A resource tuning change {recent_change_timing} lowered CPU limits while traffic "
                "shifted to the {traffic_profile} profile."
            ),
            user_visible_symptom=(
                "/data becomes slow or intermittently fails during bursts, while pods stay scheduled."
            ),
            hidden_faults=[
                "The redis-bridge sidecar now has a CPU limit of {cpu_limit}.",
            ],
            distractors=[
                "No selector or ingress objects changed.",
                "Readiness still reports healthy because /health is lightweight.",
            ],
            difficulty="medium",
            parameters={
                "cpu_limit": ["2m", "5m", "8m"],
                "cpu_burn_ms": ["900", "1200", "1500"],
                "traffic_profile": ["morning spike", "cache-warm burst", "load-test replay"],
                "recent_change_timing": ["6 minutes ago", "17 minutes ago"],
            },
            inject_commands=[
                (
                    "kubectl -n tron patch configmap app-config --type merge -p "
                    "'{\"data\":{\"BRIDGE_CPU_BURN_MS\":\"{cpu_burn_ms}\"}}' && "
                    "kubectl -n tron set resources deployment/nginx -c redis-bridge "
                    "--requests=cpu={cpu_limit},memory=64Mi "
                    "--limits=cpu={cpu_limit},memory=64Mi && "
                    "kubectl -n tron rollout restart deployment/nginx && "
                    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
                ),
            ],
            activation_checks=[
                equals(
                    "cpu-limit-applied",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].resources.limits.cpu}",
                    ],
                    "{cpu_limit}",
                ),
                equals(
                    "cpu-burn-profile-applied",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "configmap",
                        "app-config",
                        "-o",
                        "jsonpath={.data.BRIDGE_CPU_BURN_MS}",
                    ],
                    "{cpu_burn_ms}",
                ),
            ],
            cluster_clue_checks=[
                equals(
                    "cpu-limit-applied",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].resources.limits.cpu}",
                    ],
                    "{cpu_limit}",
                ),
                equals(
                    "cpu-burn-profile-applied",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "configmap",
                        "app-config",
                        "-o",
                        "jsonpath={.data.BRIDGE_CPU_BURN_MS}",
                    ],
                    "{cpu_burn_ms}",
                ),
            ],
            restore_commands=[BASE_CONFIGMAP_RESTORE, BASE_NGINX_RESTORE],
            repair_checks=[
                equals(
                    "cpu-limit-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].resources.limits.cpu}",
                    ],
                    "",
                ),
            ],
            recent_change_templates=[
                "Recent change: CPU tuning for redis-bridge landed {recent_change_timing}.",
                "Traffic hint: the service is currently seeing the {traffic_profile} profile.",
            ],
        ),
        ScenarioTemplate(
            id="memory-limits-too-low",
            kind=ScenarioKind.RESOURCE,
            title="Memory limits are too low and recent restarts hint at OOM behavior",
            trigger_context=(
                "A deployment edit {recent_change_timing} lowered memory limits before a "
                "{traffic_profile} traffic pattern."
            ),
            user_visible_symptom=(
                "/data becomes unreliable after restarts because the bridge container is now too "
                "close to its memory ceiling."
            ),
            hidden_faults=[
                "The redis-bridge sidecar now has a memory limit of {memory_limit}.",
            ],
            distractors=[
                "Redis itself is still configured normally.",
                "Readiness does not validate sustained backend work.",
            ],
            difficulty="medium",
            parameters={
                "memory_limit": ["12Mi", "16Mi", "20Mi"],
                "memory_burst_mb": ["128", "160", "192"],
                "traffic_profile": ["write-heavy burst", "mixed read/write spike"],
                "recent_change_timing": ["8 minutes ago", "21 minutes ago"],
            },
            inject_commands=[
                (
                    "kubectl -n tron patch configmap app-config --type merge -p "
                    "'{\"data\":{\"BRIDGE_MEMORY_BURST_MB\":\"{memory_burst_mb}\"}}' && "
                    "kubectl -n tron set resources deployment/nginx -c redis-bridge "
                    "--requests=cpu=25m,memory={memory_limit} "
                    "--limits=cpu=100m,memory={memory_limit} && "
                    "kubectl -n tron rollout restart deployment/nginx && "
                    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
                ),
            ],
            activation_checks=[
                equals(
                    "memory-limit-applied",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].resources.limits.memory}",
                    ],
                    "{memory_limit}",
                ),
                equals(
                    "memory-burst-profile-applied",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "configmap",
                        "app-config",
                        "-o",
                        "jsonpath={.data.BRIDGE_MEMORY_BURST_MB}",
                    ],
                    "{memory_burst_mb}",
                ),
            ],
            cluster_clue_checks=[
                shell_equals(
                    "bridge-restarts-after-memory-pressure",
                    (
                        "kubectl -n tron get pods -l app=nginx "
                        "-o jsonpath='{range .items[*].status.containerStatuses[*]}{.name}:{.restartCount}:{.lastState.terminated.reason}{\"\\n\"}{end}' "
                        "| awk -F: '$1==\"redis-bridge\" && ($2+0>0 || $3==\"OOMKilled\") {print \"memory-clue\"; exit}'"
                    ),
                    "memory-clue",
                ),
            ],
            restore_commands=[BASE_CONFIGMAP_RESTORE, BASE_NGINX_RESTORE],
            repair_checks=[
                equals(
                    "memory-limit-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].resources.limits.memory}",
                    ],
                    "",
                ),
            ],
            recent_change_templates=[
                "Recent change: memory tuning landed {recent_change_timing}.",
                "Traffic hint: the current profile matches a {traffic_profile}.",
            ],
        ),
    ]
