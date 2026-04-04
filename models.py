from __future__ import annotations

"""Core data models for the tron benchmark."""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ScenarioKind(str, Enum):
    CONFIG_DRIFT = "config_drift"
    NETWORK_POLICY = "network_policy"
    INGRESS = "ingress"
    DEPLOYMENT = "deployment"
    RESOURCE = "resource"
    SERVICE = "service"
    PROBE = "probe"
    COMPOUND = "compound"


class AgentVerdict(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"


@dataclass
class ClusterConfig:
    cluster_name: str = "tron-lab"
    namespace: str = "tron"
    kubecontext: str | None = None
    ingress_host: str = "tron.localhost"
    ingress_port: int = 8080
    manifests_dir: Path = Path("manifests")


@dataclass
class BenchmarkConfig:
    random_seed: int = 0
    max_agent_steps: int = 12
    blackbox_timeout_seconds: float = 3.0
    recent_change_limit: int = 6
    observation_line_limit: int = 50
    action_timeout_seconds: float = 20.0
    trusted_timeout_seconds: float = 180.0
    mutation_settle_seconds: float = 1.0
    work_dir: Path = Path(".")
    cluster: ClusterConfig = field(default_factory=ClusterConfig)


@dataclass
class RepairCheck:
    name: str
    command: list[str]
    success_substring: str = ""
    match_mode: str = "contains"


@dataclass
class ScenarioTemplate:
    """A reviewable incident template with deterministic mutations."""

    id: str
    kind: ScenarioKind
    title: str
    trigger_context: str
    user_visible_symptom: str
    hidden_faults: list[str]
    distractors: list[str]
    difficulty: str
    parameters: dict[str, list[Any]]
    inject_commands: list[str]
    activation_checks: list[RepairCheck]
    restore_commands: list[str]
    repair_checks: list[RepairCheck]
    blackbox_url: str = "http://127.0.0.1:8080/data"
    expected_http_status: int = 200
    recent_change_templates: list[str] = field(default_factory=list)

    @property
    def scenario_id(self) -> str:
        return self.id

    @property
    def description(self) -> str:
        return self.user_visible_symptom


@dataclass
class ScenarioInstance:
    template: ScenarioTemplate
    seed: int
    chosen_parameters: dict[str, Any]
    rendered_inject_commands: list[str]
    rendered_restore_commands: list[str]
    recent_changes: list[str]

    @property
    def rendered_commands(self) -> list[str]:
        return self.rendered_inject_commands


@dataclass
class CheckResult:
    name: str
    ok: bool
    details: str


@dataclass
class ServiceProbe:
    health_status: str
    data_status: str
    http_status: int | None
    latency_ms: int | None
    score: float


@dataclass
class ClusterSummary:
    pods: str
    services: str
    deployments: str
    endpoints: str


@dataclass
class ObservationBundle:
    incident_brief: str
    step_number: int
    last_action: str | None
    last_reward: float
    service_probe: ServiceProbe
    cluster_summary: ClusterSummary
    recent_change_hint: str

    @property
    def blackbox_status(self) -> int | None:
        return self.service_probe.http_status

    @property
    def blackbox_body(self) -> str:
        return (
            f"health={self.service_probe.health_status}, "
            f"data={self.service_probe.data_status}, "
            f"latency_ms={self.service_probe.latency_ms}"
        )

    @property
    def kubectl_get_pods(self) -> str:
        return self.cluster_summary.pods

    @property
    def kubectl_events(self) -> str:
        return self.cluster_summary.endpoints

    @property
    def recent_changes(self) -> list[str]:
        return [self.recent_change_hint]

    @property
    def hints(self) -> list[str]:
        return []


@dataclass
class AgentStep:
    command: str
    return_code: int
    stdout: str
    stderr: str
    reward: float = 0.0


@dataclass
class StepTransition:
    observation: ObservationBundle
    reward: float
    done: bool
    service_score: float
    info: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationRecord:
    scenario_id: str
    seed: int
    verdict: AgentVerdict
    score: float
    summary: str
    chosen_parameters: dict[str, Any]
    checks: list[CheckResult]
    observations: ObservationBundle
    steps: list[AgentStep]
