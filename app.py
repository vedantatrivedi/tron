from __future__ import annotations

"""Root-level compatibility shim for validators that import ``app`` directly."""

from graders import grade_easy, grade_hard, grade_medium
from server.app import app, create_app, main

__all__ = [
    "app",
    "create_app",
    "main",
    "grade_easy",
    "grade_medium",
    "grade_hard",
]
