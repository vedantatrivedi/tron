from __future__ import annotations

"""Black-box service oracle and final repair evaluation."""

import time

import requests

from tron.checks import evaluate_check
from tron.executor import CommandExecutor
from tron.models import (
    AgentStep,
    AgentVerdict,
    BenchmarkConfig,
    EvaluationRecord,
    ObservationBundle,
    ScenarioInstance,
    ServiceProbe,
)


def _probe_url(url: str, host: str, timeout: float) -> tuple[str, int | None, int | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, headers={"Host": host}, timeout=timeout)
    except requests.Timeout:
        return "timeout", None, int((time.perf_counter() - started) * 1000)
    except requests.RequestException:
        return "unreachable", None, None

    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code == 200:
        return "ok", response.status_code, latency_ms
    return "error", response.status_code, latency_ms


def probe_service(config: BenchmarkConfig) -> ServiceProbe:
    """Evaluate the live service as a black-box SLI."""

    probe_host = config.cluster.ingress_url_host or "127.0.0.1"
    base_url = f"http://{probe_host}:{config.cluster.ingress_port}"
    health_status, health_http_status, _ = _probe_url(
        f"{base_url}/health",
        config.cluster.ingress_host,
        config.blackbox_timeout_seconds,
    )
    data_status, data_http_status, latency_ms = _probe_url(
        f"{base_url}/data",
        config.cluster.ingress_host,
        config.blackbox_timeout_seconds,
    )

    if health_status == "ok" and data_status == "ok":
        score = 1.0
    elif health_status == "ok" and data_status in {"error", "timeout", "unreachable"}:
        score = 0.7
    elif health_status in {"error", "ok"} or data_status == "error":
        score = 0.4
    elif health_status == "timeout" or data_status == "timeout":
        score = 0.1
    else:
        score = 0.0

    return ServiceProbe(
        health_status=health_status,
        data_status=data_status,
        http_status=data_http_status if data_http_status is not None else health_http_status,
        latency_ms=latency_ms,
        score=score,
    )

def evaluate_repair(
    executor: CommandExecutor,
    config: BenchmarkConfig,
    instance: ScenarioInstance,
    observations: ObservationBundle,
    steps: list[AgentStep],
) -> EvaluationRecord:
    """Score final repair state from black-box health plus explicit repair checks."""

    checks = [evaluate_check(executor, check) for check in instance.template.repair_checks]
    repair_score = sum(1 for check in checks if check.ok) / max(len(checks), 1)
    probe = probe_service(config)
    score = round((repair_score + probe.score) / 2, 3)

    if repair_score == 1.0 and probe.score == 1.0:
        verdict = AgentVerdict.SUCCESS
        summary = "Service recovered and all repair checks passed."
    elif probe.score >= 0.7:
        verdict = AgentVerdict.FAILURE
        summary = "Service partially recovered but repair checks are incomplete."
    else:
        verdict = AgentVerdict.FAILURE
        summary = "Service remains degraded or unreachable."

    return EvaluationRecord(
        scenario_id=instance.template.scenario_id,
        seed=instance.seed,
        verdict=verdict,
        score=score,
        summary=summary,
        chosen_parameters=instance.chosen_parameters,
        checks=checks,
        observations=observations,
        steps=steps,
    )
