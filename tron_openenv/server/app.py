from __future__ import annotations

"""FastAPI application for the tron OpenEnv wrapper."""

import argparse
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


def _reset_route_uses_async_job() -> bool:
    return os.getenv("TRON_OPENENV_RESET_ROUTE_MODE", "").strip().lower() == "async"


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

    @app.post("/reset")
    def reset(request: Optional[ResetRequest] = None) -> object:
        try:
            if _reset_route_uses_async_job():
                return runtime.start_reset_async(request or ResetRequest())
            return runtime.reset(request or ResetRequest())
        except ClusterNotAvailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/reset_async")
    def reset_async(request: Optional[ResetRequest] = None) -> dict[str, object]:
        return runtime.start_reset_async(request or ResetRequest())

    @app.get("/reset_async/{job_id}")
    def reset_async_status(job_id: str) -> dict[str, object]:
        try:
            return runtime.get_reset_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the tron OpenEnv server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "tron_openenv.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
