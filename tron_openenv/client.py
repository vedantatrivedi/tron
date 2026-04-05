from __future__ import annotations

"""HTTP client for the tron OpenEnv server."""

from typing import Any

import requests

from tron_openenv.models import ResetRequest, ResetResponse, StepResponse, TronAction, TronState, TronTask


class TronEnvClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000", session: Any | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def _parse(self, response, model_cls):
        response.raise_for_status()
        return model_cls.model_validate(response.json())

    def _request(self, method: str, path: str, *, json_body=None, timeout: int = 30):
        request_kwargs = {}
        if json_body is not None:
            request_kwargs["json"] = json_body
        if self.session.__class__.__module__.startswith("starlette.testclient"):
            return getattr(self.session, method)(f"{self.base_url}{path}", **request_kwargs)
        return getattr(self.session, method)(f"{self.base_url}{path}", timeout=timeout, **request_kwargs)

    def tasks(self) -> list[TronTask]:
        response = self._request("get", "/tasks", timeout=30)
        response.raise_for_status()
        return [TronTask.model_validate(item) for item in response.json()]

    def reset(self, task_id: str, seed: int | None = None, hard_reset: bool = False) -> ResetResponse:
        response = self._request(
            "post",
            "/reset",
            json_body=ResetRequest(task_id=task_id, seed=seed, hard_reset=hard_reset).model_dump(),
            timeout=180,
        )
        return self._parse(response, ResetResponse)

    def step(self, command: str) -> StepResponse:
        response = self._request(
            "post",
            "/step",
            json_body=TronAction(command=command).model_dump(),
            timeout=60,
        )
        return self._parse(response, StepResponse)

    def state(self) -> TronState:
        response = self._request("get", "/state", timeout=30)
        return self._parse(response, TronState)

    def close(self) -> None:
        close_fn = getattr(self.session, "close", None)
        if callable(close_fn):
            close_fn()
