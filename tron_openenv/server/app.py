from __future__ import annotations

"""FastAPI application for the tron OpenEnv wrapper."""

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

from tron_openenv.models import ResetRequest, ResetResponse, StepResponse, TronAction, TronState, TronTask
from tron_openenv.server.environment import ClusterNotAvailableError, TronOpenEnvService


def create_app(service: TronOpenEnvService | None = None) -> FastAPI:
    runtime = service or TronOpenEnvService()
    app = FastAPI(title="tron OpenEnv server", version="1.0.0")

    def metadata_payload() -> dict[str, object]:
        return {
            "name": "tron",
            "status": "ok",
            "tasks": [task.model_dump() for task in runtime.list_tasks()],
        }

    @app.get("/")
    def root() -> dict[str, object]:
        return metadata_payload()

    @app.get("/info")
    def info() -> dict[str, object]:
        return metadata_payload()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/tasks", response_model=list[TronTask])
    def tasks() -> list[TronTask]:
        return runtime.list_tasks()

    @app.post("/reset", response_model=ResetResponse)
    def reset(request: Optional[ResetRequest] = None) -> ResetResponse:
        try:
            return runtime.reset(request or ResetRequest())
        except ClusterNotAvailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/step", response_model=StepResponse)
    def step(action: TronAction) -> StepResponse:
        try:
            return runtime.step(action)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/state", response_model=TronState)
    def state() -> TronState:
        return runtime.state()

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "tron_openenv.server.app:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
    )
