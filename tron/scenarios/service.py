from __future__ import annotations

"""Service and endpoint scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import BASE_REDIS_RESTORE, equals


def build_service_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="service-selector-mismatch",
            kind=ScenarioKind.SERVICE,
            title="Redis service selector drifted after a label change",
            trigger_context=(
                "A service edit {recent_change_timing} changed the selector during a label tidy-up."
            ),
            user_visible_symptom=(
                "/data fails because the redis service no longer selects any healthy backends."
            ),
            hidden_faults=[
                "The redis service selector now points at app={selector_app}.",
            ],
            distractors=[
                "Redis pods still exist and are Ready.",
                "Nginx pods remain Ready and ingress still routes to them.",
            ],
            difficulty="medium",
            parameters={
                "selector_app": ["redis-canary", "redis-shadow", "redis-v2"],
                "endpoint_loss": ["full endpoint loss", "near-total endpoint loss"],
                "recent_change_timing": ["9 minutes ago", "24 minutes ago"],
            },
            inject_commands=[
                "kubectl -n tron patch service redis --type merge -p '{\"spec\":{\"selector\":{\"app\":\"{selector_app}\"}}}'",
            ],
            activation_checks=[
                equals(
                    "service-selector-drifted",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "service",
                        "redis",
                        "-o",
                        "jsonpath={.spec.selector.app}",
                    ],
                    "{selector_app}",
                ),
                equals(
                    "redis-endpoints-empty",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "endpoints",
                        "redis",
                        "-o",
                        "jsonpath={.subsets}",
                    ],
                    "",
                ),
            ],
            cluster_clue_checks=[
                equals(
                    "redis-endpoints-empty",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "endpoints",
                        "redis",
                        "-o",
                        "jsonpath={.subsets}",
                    ],
                    "",
                ),
            ],
            restore_commands=[BASE_REDIS_RESTORE],
            repair_checks=[
                equals(
                    "service-selector-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "service",
                        "redis",
                        "-o",
                        "jsonpath={.spec.selector.app}",
                    ],
                    "redis",
                ),
            ],
            recent_change_templates=[
                "Recent change: the redis service selector was edited {recent_change_timing}.",
                "Blast radius hint: this caused {endpoint_loss}.",
            ],
        ),
    ]
