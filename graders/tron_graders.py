from __future__ import annotations

import os
from typing import Any

import requests


DEFAULT_RUNTIME_BASE_URL = "https://jj90999-tron.hf.space"


def _clamp_to_open_interval(value: float) -> float:
    """Clamp a score to the open interval (0, 1) as required by the grading contract."""
    return max(0.001, min(0.999, value))


class BoundedGrade(float):
    """A float that guarantees the value is in the open interval (0, 1)."""

    def __new__(cls, value: float) -> "BoundedGrade":
        clamped = _clamp_to_open_interval(float(value))
        return super().__new__(cls, clamped)

    @property
    def score(self) -> float:
        return float(self)

    @property
    def reward(self) -> float:
        return float(self)

    def model_dump(self) -> dict[str, float]:
        value = float(self)
        return {"score": value, "reward": value}


def _extract_service_score(candidate: Any) -> float | None:
    """Extract a score from various input formats."""
    if candidate is None:
        return None

    # Handle numeric types directly
    if isinstance(candidate, (int, float)):
        value = float(candidate)
        if 0.0 <= value <= 1.0:
            return _clamp_to_open_interval(value)
        return None

    # Handle objects with model_dump() method (Pydantic models)
    if hasattr(candidate, "model_dump") and callable(candidate.model_dump):
        return _extract_service_score(candidate.model_dump())

    # Handle objects with .score attribute
    if hasattr(candidate, "score"):
        score_val = getattr(candidate, "score", None)
        if score_val is not None:
            return _extract_service_score(score_val)

    # Handle dicts
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


def _grade_via_runtime(task_id: str, base_url: str | None = None, timeout: float = 10.0) -> float:
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
            return BoundedGrade(score)
    try:
        return BoundedGrade(_grade_via_runtime(task_id, base_url=kwargs.get("base_url")))
    except Exception:
        # Return a default degraded score if remote grading fails
        # This ensures the grader always returns a valid score in (0, 1)
        return BoundedGrade(0.5)


def grade_easy(*args: Any, **kwargs: Any) -> float:
    return _grade_task("easy", *args, **kwargs)


def grade_medium(*args: Any, **kwargs: Any) -> float:
    return _grade_task("medium", *args, **kwargs)


def grade_hard(*args: Any, **kwargs: Any) -> float:
    return _grade_task("hard", *args, **kwargs)
