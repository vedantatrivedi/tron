from __future__ import annotations

"""Probe-related scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import BASE_NGINX_RESTORE, equals


def build_probe_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="readiness-probe-too-permissive",
            kind=ScenarioKind.PROBE,
            title="Readiness probe was loosened so rollouts look healthy too early",
            trigger_context=(
                "A rollout safety edit {recent_change_timing} replaced the nginx readiness probe "
                "with an unconditional exec check."
            ),
            user_visible_symptom=(
                "Rollouts appear healthy even when they are not validating the real data path."
            ),
            hidden_faults=[
                "The nginx readiness probe now always exits 0 instead of checking HTTP behavior.",
            ],
            distractors=[
                "The frontend still answers /health immediately.",
                "No service or ingress objects changed.",
            ],
            difficulty="easy",
            parameters={
                "recent_change_timing": ["4 minutes ago", "13 minutes ago"],
                "rollout_state": ["still progressing", "recently completed"],
            },
            inject_commands=[
                (
                    "kubectl -n tron patch deployment nginx --type=json -p "
                    "'["
                    "{\"op\":\"remove\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/httpGet\"},"
                    "{\"op\":\"add\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/exec\","
                    "\"value\":{\"command\":[\"sh\",\"-c\",\"exit 0\"]}},"
                    "{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/initialDelaySeconds\",\"value\":1},"
                    "{\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/readinessProbe/periodSeconds\",\"value\":5}"
                    "]' && "
                    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
                ),
            ],
            activation_checks=[
                equals(
                    "probe-now-uses-exec",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[0].readinessProbe.exec.command[0]}",
                    ],
                    "sh",
                ),
            ],
            restore_commands=[BASE_NGINX_RESTORE],
            repair_checks=[
                equals(
                    "probe-http-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "deployment",
                        "nginx",
                        "-o",
                        "jsonpath={.spec.template.spec.containers[0].readinessProbe.httpGet.path}",
                    ],
                    "/health",
                ),
            ],
            requires_service_degradation=False,
            recent_change_templates=[
                "Recent change: rollout safety settings were edited {recent_change_timing}.",
                "Rollout note: the new deployment is {rollout_state}.",
            ],
        ),
    ]
