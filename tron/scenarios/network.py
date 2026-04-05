from __future__ import annotations

"""Network policy scenario definitions."""

from tron.models import ScenarioKind, ScenarioTemplate
from tron.scenarios.common import BASE_NETWORKPOLICY_RESTORE, contains, equals


def build_network_scenarios() -> list[ScenarioTemplate]:
    return [
        ScenarioTemplate(
            id="networkpolicy-blocks-nginx-to-redis",
            kind=ScenarioKind.NETWORK_POLICY,
            title="NetworkPolicy blocks nginx to redis",
            trigger_context=(
                "A namespace policy change {recent_change_timing} introduced an egress deny rule."
            ),
            user_visible_symptom=(
                "/health still passes, but /data fails because nginx can no longer connect to redis."
            ),
            hidden_faults=[
                "A deny-egress NetworkPolicy now selects nginx pods.",
            ],
            distractors=[
                "Services and ConfigMaps are unchanged.",
                "Redis pods remain healthy inside the namespace.",
            ],
            difficulty="easy",
            parameters={
                "policy_name": ["block-redis-egress", "deny-nginx-egress"],
                "recent_change_timing": ["3 minutes ago", "14 minutes ago", "26 minutes ago"],
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
            ],
            restore_commands=[
                "kubectl -n tron delete networkpolicy {policy_name} --ignore-not-found",
                BASE_NETWORKPOLICY_RESTORE,
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
            ],
            recent_change_templates=[
                "Recent change: a deny-egress policy was applied {recent_change_timing}.",
            ],
        ),
    ]
