from __future__ import annotations

"""Ingress scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import BASE_INGRESS_RESTORE, equals


def build_ingress_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="ingress-path-rewrite-bug",
            kind=ScenarioKind.INGRESS,
            title="Ingress path rewrite bug breaks external routing",
            trigger_context=(
                "An ingress edit {recent_change_timing} shipped a bad path rewrite and the root "
                "path now routes as {broken_path}."
            ),
            user_visible_symptom=(
                "External requests stop reaching nginx on the expected path, even though in-cluster "
                "pods and services remain healthy."
            ),
            hidden_faults=[
                "The ingress path moved away from / and now only matches {broken_path}.",
            ],
            distractors=[
                "Nginx pods still answer traffic when accessed internally.",
                "The redis backend is unchanged.",
            ],
            difficulty="medium",
            parameters={
                "broken_path": ["/broken", "/v1", "/internal-only"],
                "recent_change_timing": ["10 minutes ago", "27 minutes ago"],
                "endpoint_loss": ["full external loss", "external loss except for debug paths"],
            },
            inject_commands=[
                "kubectl -n tron patch ingress tron-ingress --type json -p '[{\"op\":\"replace\",\"path\":\"/spec/rules/0/http/paths/0/path\",\"value\":\"{broken_path}\"}]'",
            ],
            activation_checks=[
                equals(
                    "ingress-path-drifted",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "ingress",
                        "tron-ingress",
                        "-o",
                        "jsonpath={.spec.rules[0].http.paths[0].path}",
                    ],
                    "{broken_path}",
                ),
            ],
            cluster_clue_checks=[
                equals(
                    "ingress-path-drifted",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "ingress",
                        "tron-ingress",
                        "-o",
                        "jsonpath={.spec.rules[0].http.paths[0].path}",
                    ],
                    "{broken_path}",
                ),
            ],
            restore_commands=[BASE_INGRESS_RESTORE],
            repair_checks=[
                equals(
                    "ingress-path-restored",
                    [
                        "kubectl",
                        "-n",
                        "tron",
                        "get",
                        "ingress",
                        "tron-ingress",
                        "-o",
                        "jsonpath={.spec.rules[0].http.paths[0].path}",
                    ],
                    "/",
                ),
            ],
            recent_change_templates=[
                "Recent change: ingress path handling changed {recent_change_timing}.",
                "Blast radius hint: this caused {endpoint_loss}.",
            ],
        ),
    ]
