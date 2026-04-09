from __future__ import annotations

import os
from typing import Any

import requests


DEFAULT_RUNTIME_BASE_URL = "https://jj90999-tron.hf.space"


def _extract_service_score(candidate: Any) -> float | None:
    if candidate is None:
        return None
    if isinstance(candidate, (int, float)):
        value = float(candidate)
        return value if 0.0 < value < 1.0 else None
    if isinstance(candidate, dict):
        for key in ("score", "reward"):
            value = _extract_service_score(candidate.get(key))
            if value is not None:
                return value
        service_probe = candidate.get("service_probe")
        if isinstance(service_probe, dict):
            return _extract_service_score(service_probe.get("score"))
        observation = candidate.get("observation")
        if isinstance(observation, dict):
            return _extract_service_score(observation)
    return None


def _runtime_base_url(explicit_base_url: str | None = None) -> str:
    return (
        explicit_base_url
        or os.getenv("TRON_GRADER_BASE_URL")
        or os.getenv("ENV_BASE_URL")
        or DEFAULT_RUNTIME_BASE_URL
    ).rstrip("/")


def _grade_via_runtime(task_id: str, base_url: str | None = None, timeout: float = 120.0) -> float:
    response = requests.post(
        f"{_runtime_base_url(base_url)}/grader/{task_id}",
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    score = _extract_service_score(payload)
    if score is None:
        raise RuntimeError(f"grader endpoint returned no bounded score for task {task_id}: {payload!r}")
    return score


def _grade_task(task_id: str, *args: Any, **kwargs: Any) -> float:
    for candidate in [*args, *kwargs.values()]:
        score = _extract_service_score(candidate)
        if score is not None:
            return score
    return _grade_via_runtime(task_id, base_url=kwargs.get("base_url"))


def grade_easy(*args: Any, **kwargs: Any) -> float:
    return _grade_task("easy", *args, **kwargs)


def grade_medium(*args: Any, **kwargs: Any) -> float:
    return _grade_task("medium", *args, **kwargs)


def grade_hard(*args: Any, **kwargs: Any) -> float:
    return _grade_task("hard", *args, **kwargs)
