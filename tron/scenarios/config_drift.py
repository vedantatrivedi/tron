from __future__ import annotations

"""Config-drift scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import (
    BASE_CONFIGMAP_RESTORE,
    RESTART_NGINX,
    equals,
    shell_equals,
)


def build_config_drift_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="bad-rollout-wrong-redis-host",
            kind=ScenarioKind.CONFIG_DRIFT,
            title="Bad rollout points nginx pods at the wrong Redis host",
            trigger_context=(
                "A deployment rollout {rollout_state} after a ConfigMap edit {recent_change_timing}."
            ),
            user_visible_symptom=(
                "/health stays green, but /data returns errors because nginx pods now use "
                "REDIS_HOST={bad_host}."
            ),
            hidden_faults=[
                "The app-config ConfigMap now points at a non-existent Redis host.",
                "The nginx deployment consumed that bad value during a rollout.",
            ],
            distractors=[
                "Pods are Ready because readiness only checks /health.",
                "Ingress and services still point at the expected objects.",
            ],
            difficulty="easy",
            parameters={
                "bad_host": ["redis-bad", "redis-shadow", "redis-primary-typo"],
                "rollout_state": ["is still finishing", "finished cleanly"],
                "recent_change_timing": ["7 minutes ago", "18 minutes ago", "34 minutes ago"],
            },
            inject_commands=[
                "kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"{bad_host}\"}}'",
                RESTART_NGINX,
            ],
            activation_checks=[
                equals(
                    "configmap-has-bad-host",
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
                    "{bad_host}",
                ),
                shell_equals(
                    "running-pod-uses-bad-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
            ],
            cluster_clue_checks=[
                equals(
                    "configmap-has-bad-host",
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
                    "{bad_host}",
                ),
            ],
            restore_commands=[BASE_CONFIGMAP_RESTORE, RESTART_NGINX],
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
            ],
            recent_change_templates=[
                "Recent change: app-config REDIS_HOST was updated as part of a rollout that {rollout_state}.",
                "Change timing hint: the rollout started {recent_change_timing}.",
            ],
        ),
        ScenarioTemplate(
            id="configmap-fixed-but-pods-stale",
            kind=ScenarioKind.CONFIG_DRIFT,
            title="ConfigMap looks healthy but live pods still run stale env",
            trigger_context=(
                "An operator reverted a bad ConfigMap edit {recent_change_timing}, but no fresh "
                "rollout happened afterward."
            ),
            user_visible_symptom=(
                "kubectl get configmap shows REDIS_HOST=redis, yet /data still fails because "
                "running pods kept the old bad env."
            ),
            hidden_faults=[
                "Pods were restarted while REDIS_HOST was wrong.",
                "The ConfigMap was reverted without restarting nginx afterward.",
            ],
            distractors=[
                "The current ConfigMap contents look correct.",
                "Ingress and service objects are unchanged.",
            ],
            difficulty="medium",
            parameters={
                "bad_host": ["redis-bad", "redis-shadow"],
                "recent_change_timing": ["5 minutes ago", "11 minutes ago", "29 minutes ago"],
            },
            inject_commands=[
                "kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"{bad_host}\"}}'",
                RESTART_NGINX,
                "kubectl -n tron patch configmap app-config --type merge -p '{\"data\":{\"REDIS_HOST\":\"redis\"}}'",
            ],
            activation_checks=[
                equals(
                    "configmap-looks-healthy",
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
                shell_equals(
                    "running-pod-still-has-stale-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
            ],
            cluster_clue_checks=[
                shell_equals(
                    "running-pod-still-has-stale-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
            ],
            restore_commands=[RESTART_NGINX],
            repair_checks=[
                shell_equals(
                    "new-pods-use-restored-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "redis",
                ),
            ],
            recent_change_templates=[
                "Recent change: app-config was reverted {recent_change_timing}.",
                "Rollout note: pods were not restarted after the revert.",
            ],
        ),
    ]
