from __future__ import annotations

"""Shared helpers for command-backed scenario and oracle checks."""

from typing import Protocol

from tron.models import CheckResult, RepairCheck


class SupportsCheckExecution(Protocol):
    def run_argv(self, argv: list[str], timeout: float = 20.0): ...


def evaluate_check(executor: SupportsCheckExecution, check: RepairCheck) -> CheckResult:
    result = executor.run_argv(check.command)
    stdout = result.stdout
    details = stdout or result.stderr
    if check.match_mode == "equals":
        ok = result.return_code == 0 and stdout == check.success_substring
    elif check.success_substring:
        ok = result.return_code == 0 and check.success_substring in stdout
    else:
        ok = result.return_code == 0 and stdout == ""
    return CheckResult(name=check.name, ok=ok, details=details)


def format_failed_checks(prefix: str, checks: list[CheckResult]) -> str:
    failed = [check for check in checks if not check.ok]
    if not failed:
        return ""
    return prefix + ", ".join(f"{check.name} -> {check.details}" for check in failed)
