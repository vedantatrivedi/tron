from __future__ import annotations

"""Compatibility shim for the canonical tron OpenEnv server.

The supported FastAPI application lives in ``tron_openenv.server.app`` and is
the only server implementation used by Docker, the OpenEnv contract, and the
README. This module exists so older local commands like ``python app.py`` keep
working without creating a second divergent API surface for reviewers.
"""

import os

import uvicorn

from tron_openenv.server.app import app, create_app

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
