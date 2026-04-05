from __future__ import annotations

"""Deployment and rollout scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import BASE_NGINX_RESTORE, equals


def build_deployment_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="bridge-crashloop-bad-command",
            kind=ScenarioKind.DEPLOYMENT,
            title="Bad rollout changes the bridge command and causes a sidecar crash loop",
            trigger_context=(
                "A deployment edit {recent_change_timing} changed the redis-bridge startup command "
                "during a rollout."
            ),
            user_visible_symptom=(
                "/health stays green, but /data fails because the redis-bridge sidecar now crashes "
                "instead of serving requests."
            ),
            hidden_faults=[
                "The redis-bridge container command now points at a missing script.",
            ],
            distractors=[
                "The nginx frontend container still starts and answers /health.",
                "ConfigMaps, services, and ingress objects remain unchanged.",
            ],
            difficulty="medium",
            parameters={
                "recent_change_timing": ["9 minutes ago", "22 minutes ago"],
                "bad_script_path": ["/app/missing.py", "/app/bridge-moved.py"],
            },
            inject_commands=[
                (
                    "kubectl -n tron patch deployment nginx --type=json -p "
                    "'["
                    "{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/1/command/1\","
                    "\"value\":\"{bad_script_path}\"}"
                    "]' && "
                    "kubectl -n tron rollout restart deployment/nginx && "
                    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
                ),
            ],
            activation_checks=[
                equals(
                    "bridge-command-drifted",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].command[1]}",
                    ],
                    "{bad_script_path}",
                ),
            ],
            cluster_clue_checks=[
                equals(
                    "bridge-command-drifted",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].command[1]}",
                    ],
                    "{bad_script_path}",
                ),
            ],
            restore_commands=[BASE_NGINX_RESTORE],
            repair_checks=[
                equals(
                    "bridge-command-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[1].command[1]}",
                    ],
                    "/app/bridge.py",
                ),
            ],
            recent_change_templates=[
                "Recent change: redis-bridge startup command was edited {recent_change_timing}.",
            ],
        ),
        ScenarioTemplate(
            id="deployment-scaled-to-zero",
            kind=ScenarioKind.DEPLOYMENT,
            title="Deployment was accidentally scaled to zero during a cleanup",
            trigger_context=(
                "A cleanup change {recent_change_timing} scaled the frontend deployment down to zero "
                "replicas."
            ),
            user_visible_symptom=(
                "The service becomes unreachable because there are no nginx pods left serving traffic."
            ),
            hidden_faults=[
                "The nginx deployment now has replicas=0.",
            ],
            distractors=[
                "Ingress and services still exist and point at nginx.",
                "Redis is still healthy in-cluster.",
            ],
            difficulty="easy",
            parameters={
                "recent_change_timing": ["5 minutes ago", "19 minutes ago"],
                "cleanup_window": ["post-release cleanup", "overnight capacity trim"],
            },
            inject_commands=[
                "kubectl -n tron patch deployment nginx --type merge -p '{\"spec\":{\"replicas\":0}}'",
            ],
            activation_checks=[
                equals(
                    "nginx-scaled-to-zero",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.replicas}",
                    ],
                    "0",
                ),
            ],
            cluster_clue_checks=[
                equals(
                    "nginx-scaled-to-zero",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.replicas}",
                    ],
                    "0",
                ),
            ],
            restore_commands=[BASE_NGINX_RESTORE],
            repair_checks=[
                equals(
                    "nginx-replicas-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.replicas}",
                    ],
                    "1",
                ),
            ],
            recent_change_templates=[
                "Recent change: nginx replicas were reduced during {cleanup_window} {recent_change_timing}.",
            ],
        ),
    ]
