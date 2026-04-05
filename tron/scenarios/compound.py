from __future__ import annotations

"""Compound scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import (
    BASE_CONFIGMAP_RESTORE,
    BASE_NETWORKPOLICY_RESTORE,
    BASE_NGINX_RESTORE,
    BASE_REDIS_RESTORE,
    RESTART_NGINX,
    contains,
    equals,
    shell_equals,
)


def build_compound_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="wrong-redis-host-plus-cpu-throttle",
            kind=ScenarioKind.COMPOUND,
            title="Wrong Redis host and CPU throttling combine during a traffic increase",
            trigger_context=(
                "A rollout {recent_change_timing} changed REDIS_HOST and a later resource tune hit "
                "the {traffic_profile} profile."
            ),
            user_visible_symptom=(
                "/data is broken outright for some requests and slow for others because the host is "
                "wrong and the bridge is heavily CPU constrained."
            ),
            hidden_faults=[
                "REDIS_HOST is set to {bad_host}.",
                "The redis-bridge sidecar CPU limit was reduced to {cpu_limit}.",
            ],
            distractors=[
                "Pods still appear Ready.",
                "Ingress and services did not change in this scenario.",
            ],
            difficulty="hard",
            parameters={
                "bad_host": ["redis-bad", "redis-shadow"],
                "cpu_limit": ["5m", "10m"],
                "traffic_profile": ["traffic increase", "batch replay"],
                "recent_change_timing": ["12 minutes ago", "31 minutes ago"],
            },
            inject_commands=[
                "kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"{bad_host}\"}}'",
                RESTART_NGINX,
                (
                    "kubectl -n tron set resources deployment/nginx -c redis-bridge "
                    "--requests=cpu={cpu_limit},memory=64Mi "
                    "--limits=cpu={cpu_limit},memory=64Mi && "
                    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
                ),
            ],
            activation_checks=[
                shell_equals(
                    "running-pod-uses-bad-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
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
            ],
            cluster_clue_checks=[
                shell_equals(
                    "running-pod-uses-bad-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
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
            ],
            restore_commands=[BASE_CONFIGMAP_RESTORE, BASE_NGINX_RESTORE, RESTART_NGINX],
            repair_checks=[
                equals(
                    "configmap-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "configmap",
                        "app-config",
                        "-o",
                        "jsonpath={.data.REDIS_HOST}",
                    ],
                    "redis",
                ),
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
                "Recent change: REDIS_HOST changed during a rollout {recent_change_timing}.",
                "Traffic hint: the current profile looks like a {traffic_profile}.",
            ],
        ),
        ScenarioTemplate(
            id="networkpolicy-plus-secondary-drift",
            kind=ScenarioKind.COMPOUND,
            title="NetworkPolicy regression overlaps with a second stale or selector issue",
            trigger_context=(
                "A deny-egress policy landed {recent_change_timing}, and a second change left the "
                "cluster in the {secondary_variant} state."
            ),
            user_visible_symptom=(
                "/data remains broken after fixing one obvious issue because a second drifted object "
                "is also involved."
            ),
            hidden_faults=[
                "A deny-egress NetworkPolicy blocks nginx.",
                "A second issue is present: {secondary_variant}.",
            ],
            distractors=[
                "Pods still look generally healthy.",
                "Recent changes mention both networking and application configuration.",
            ],
            difficulty="hard",
            parameters={
                "policy_name": ["block-redis-egress", "deny-nginx-egress"],
                "recent_change_timing": ["15 minutes ago", "28 minutes ago"],
                "secondary_variant": ["stale config", "selector mismatch"],
                "distractor_note": ["review-window-a", "review-window-b", "audit-followup"],
                "secondary_inject_command": [
                    "kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"redis-shadow\"}}' && kubectl -n tron rollout restart deployment/nginx && kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"redis\"}}'",
                    "kubectl -n tron patch service redis --type merge -p '{\"spec\":{\"selector\":{\"app\":\"redis-shadow\"}}}'",
                ],
                "secondary_activation_script": [
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "kubectl -n tron get service redis -o jsonpath='{.spec.selector.app}'",
                ],
                "secondary_activation_expected": ["redis-shadow", "redis-shadow"],
                "secondary_repair_script": [
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "kubectl -n tron get service redis -o jsonpath='{.spec.selector.app}'",
                ],
                "secondary_repair_expected": ["redis", "redis"],
            },
            inject_commands=[
                """cat <<'EOF' | kubectl apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {policy_name}
  namespace: tron
spec:
  podSelector:
    matchLabels:
      app: nginx
  policyTypes:
  - Egress
  egress: []
EOF""",
                "{secondary_inject_command}",
            ],
            distractor_commands=[
                (
                    "kubectl -n tron annotate ingress tron-ingress "
                    "tron.dev/review-note={distractor_note} --overwrite"
                ),
            ],
            activation_checks=[
                contains(
                    "policy-present",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "networkpolicy",
                        "{policy_name}",
                        "-o",
                        "name",
                    ],
                    "networkpolicy.networking.k8s.io/",
                ),
                shell_equals(
                    "secondary-issue-present",
                    "{secondary_activation_script}",
                    "{secondary_activation_expected}",
                ),
            ],
            cluster_clue_checks=[
                contains(
                    "policy-present",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "networkpolicy",
                        "{policy_name}",
                        "-o",
                        "name",
                    ],
                    "networkpolicy.networking.k8s.io/",
                ),
                shell_equals(
                    "secondary-issue-present",
                    "{secondary_activation_script}",
                    "{secondary_activation_expected}",
                ),
            ],
            restore_commands=[
                "kubectl -n tron delete networkpolicy {policy_name} --ignore-not-found",
                BASE_CONFIGMAP_RESTORE,
                BASE_REDIS_RESTORE,
                BASE_NETWORKPOLICY_RESTORE,
                RESTART_NGINX,
            ],
            distractor_restore_commands=[
                "kubectl -n tron annotate ingress tron-ingress tron.dev/review-note- --overwrite",
            ],
            repair_checks=[
                equals(
                    "policy-removed",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "networkpolicy",
                        "{policy_name}",
                        "--ignore-not-found",
                        "-o",
                        "name",
                    ],
                    "",
                ),
                shell_equals(
                    "secondary-issue-cleared",
                    "{secondary_repair_script}",
                    "{secondary_repair_expected}",
                ),
            ],
            recent_change_templates=[
                "Recent change: the policy regression landed {recent_change_timing}.",
                "Debug hint: the second issue lines up with {secondary_variant}.",
                "Unrelated change: ingress metadata was updated for {distractor_note}.",
            ],
        ),
    ]
