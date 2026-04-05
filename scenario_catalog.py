from __future__ import annotations

"""Structured incident catalog for the tron benchmark."""

from models import RepairCheck, ScenarioKind, ScenarioTemplate


DATA_URL = "http://127.0.0.1:8080/data"
BASE_CONFIGMAP_RESTORE = "kubectl apply -f manifests/configmap.yaml"
BASE_NGINX_RESTORE = (
    "kubectl apply -f manifests/nginx.yaml && "
    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
)
BASE_REDIS_RESTORE = (
    "kubectl apply -f manifests/redis.yaml && "
    "kubectl -n tron rollout status deployment/redis --timeout=120s"
)
BASE_INGRESS_RESTORE = "kubectl apply -f manifests/ingress.yaml"
BASE_NETWORKPOLICY_RESTORE = "kubectl apply -f manifests/networkpolicy-base.yaml"
RESTART_NGINX = (
    "kubectl -n tron rollout restart deployment/nginx && "
    "kubectl -n tron rollout status deployment/nginx --timeout=120s"
)


def _equals(name: str, command: list[str], expected: str) -> RepairCheck:
    return RepairCheck(name=name, command=command, success_substring=expected, match_mode="equals")


def _contains(name: str, command: list[str], expected: str) -> RepairCheck:
    return RepairCheck(name=name, command=command, success_substring=expected, match_mode="contains")


def _shell_equals(name: str, script: str, expected: str) -> RepairCheck:
    return _equals(name, ["sh", "-lc", script], expected)


def load_catalog() -> list[ScenarioTemplate]:
    """Return the full incident catalog."""

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
                _equals(
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
                _shell_equals(
                    "running-pod-uses-bad-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
            ],
            restore_commands=[BASE_CONFIGMAP_RESTORE, RESTART_NGINX],
            repair_checks=[
                _equals(
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
                _equals(
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
                _shell_equals(
                    "running-pod-still-has-stale-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
            ],
            restore_commands=[RESTART_NGINX],
            repair_checks=[
                _shell_equals(
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
                _equals(
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
                _equals(
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
                _equals(
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
                "cpu_limit": ["5m", "10m", "15m"],
                "cpu_burn_ms": ["400", "500", "650"],
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
                _equals(
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
            restore_commands=[BASE_CONFIGMAP_RESTORE, BASE_NGINX_RESTORE],
            repair_checks=[
                _equals(
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
                "memory_limit": ["24Mi", "32Mi", "40Mi"],
                "memory_burst_mb": ["48", "56", "64"],
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
                _equals(
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
            ],
            restore_commands=[BASE_CONFIGMAP_RESTORE, BASE_NGINX_RESTORE],
            repair_checks=[
                _equals(
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
                _equals(
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
                _equals(
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
                _contains(
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
                _equals(
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
                _equals(
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
                _equals(
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
                _shell_equals(
                    "running-pod-uses-bad-host",
                    "kubectl -n tron exec deployment/nginx -c redis-bridge -- printenv REDIS_HOST",
                    "{bad_host}",
                ),
                _equals(
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
                _equals(
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
                _equals(
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
                _contains(
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
                _shell_equals(
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
                _equals(
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
                _shell_equals(
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
                _equals(
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
                _equals(
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
                _equals(
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
                _equals(
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
