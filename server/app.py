from __future__ import annotations

"""Compatibility shim for OpenEnv's canonical ``server/app.py`` layout."""

from tron_openenv.server.app import app, create_app, main

__all__ = ["app", "create_app", "main"]


if __name__ == "__main__":
    main()
