from __future__ import annotations

"""Compatibility shim for OpenEnv's canonical ``server/app.py`` layout."""

from graders import (
    EasyGrader,
    HardGrader,
    MediumGrader,
    grade_easy,
    grade_hard,
    grade_medium,
)
from tron_openenv.server.app import app, create_app
from tron_openenv.server.app import main as _main

__all__ = [
    "app",
    "create_app",
    "main",
    "EasyGrader",
    "MediumGrader",
    "HardGrader",
    "grade_easy",
    "grade_medium",
    "grade_hard",
]


def main() -> None:
    _main()


if __name__ == "__main__":
    main()
