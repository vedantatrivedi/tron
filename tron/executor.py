from __future__ import annotations

"""Command execution helpers for the benchmark runtime."""

import shlex
import subprocess
from dataclasses import dataclass


ALLOWED_ACTION_BINARIES = {"kubectl", "curl"}
ZERO_COST_SUBCOMMANDS = {
    ("kubectl", "get"),
    ("kubectl", "describe"),
    ("kubectl", "logs"),
    ("kubectl", "top"),
    ("kubectl", "rollout", "history"),
    ("curl",),
}


@dataclass
class CommandResult:
    command: str
    return_code: int
    stdout: str
    stderr: str
    rejected: bool = False
    timed_out: bool = False
    action_cost: float = 0.0


class CommandExecutor:
    def __init__(
        self,
        cwd: str | None = None,
        output_limit: int = 4000,
        kubeconfig_path: str | None = None,
    ) -> None:
        self.cwd = cwd
        self.output_limit = output_limit
        self.kubeconfig_path = kubeconfig_path

    def _augment_kubectl_argv(self, argv: list[str]) -> list[str]:
        if not self.kubeconfig_path or not argv or argv[0] != "kubectl":
            return argv
        if any(part == "--kubeconfig" or part.startswith("--kubeconfig=") for part in argv[1:]):
            return argv
        return ["kubectl", "--kubeconfig", self.kubeconfig_path, *argv[1:]]

    def _augment_kubectl_command(self, command: str) -> str:
        if not self.kubeconfig_path:
            return command
        kubeconfig_flag = f"--kubeconfig {shlex.quote(self.kubeconfig_path)}"
        updated = command
        if updated.startswith("kubectl "):
            updated = updated.replace("kubectl ", f"kubectl {kubeconfig_flag} ", 1)
        updated = updated.replace("&& kubectl ", f"&& kubectl {kubeconfig_flag} ")
        return updated

    def _coerce_text(self, value: str | bytes) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value

    def _truncate(self, text: str | bytes) -> str:
        text = self._coerce_text(text)
        stripped = text.strip()
        if len(stripped) <= self.output_limit:
            return stripped
        return stripped[: self.output_limit].rstrip() + "\n...[truncated]"

    def run(self, command: str, timeout: float = 20.0) -> CommandResult:
        command = self._augment_kubectl_command(command)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=self.cwd,
                text=True,
                timeout=timeout,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=command,
                return_code=124,
                stdout=self._truncate(exc.stdout or ""),
                stderr=self._truncate(exc.stderr or "command timed out"),
                timed_out=True,
            )
        return CommandResult(
            command=command,
            return_code=completed.returncode,
            stdout=self._truncate(completed.stdout),
            stderr=self._truncate(completed.stderr),
        )

    def run_argv(self, argv: list[str], timeout: float = 20.0) -> CommandResult:
        argv = self._augment_kubectl_argv(argv)
        try:
            completed = subprocess.run(
                argv,
                cwd=self.cwd,
                text=True,
                timeout=timeout,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=shlex.join(argv),
                return_code=124,
                stdout=self._truncate(exc.stdout or ""),
                stderr=self._truncate(exc.stderr or "command timed out"),
                timed_out=True,
            )
        return CommandResult(
            command=shlex.join(argv),
            return_code=completed.returncode,
            stdout=self._truncate(completed.stdout),
            stderr=self._truncate(completed.stderr),
        )

    def validate_action(self, command: str) -> tuple[bool, str]:
        stripped = command.strip()
        if not stripped:
            return False, "empty command"
        try:
            argv = shlex.split(stripped)
        except ValueError as exc:
            return False, f"invalid shell quoting: {exc}"
        if not argv:
            return False, "empty command"
        if argv[0] not in ALLOWED_ACTION_BINARIES:
            return False, "only kubectl and curl actions are allowed"
        if argv[0] == "kubectl" and "edit" in argv[1:]:
            return False, "kubectl edit is not allowed in this non-interactive benchmark"
        if argv[0] == "kubectl" and "scale" in argv[1:]:
            return False, "kubectl scale is not allowed because it bypasses benchmark repairs"
        if argv and argv[0] == "kubectl":
            resource_tokens = {token.lower() for token in argv[1:]}
            if {"rs", "replicaset", "replicasets"} & resource_tokens:
                return False, "direct ReplicaSet mutation is not allowed in this benchmark"
        if any(token in {"|", "&&", "||", ";", ">", ">>", "<"} for token in argv):
            return False, "shell control operators are not allowed"
        return True, ""

    def action_cost(self, command: str) -> float:
        argv = shlex.split(command)
        if tuple(argv[:1]) in ZERO_COST_SUBCOMMANDS or tuple(argv[:2]) in ZERO_COST_SUBCOMMANDS:
            return 0.0
        if tuple(argv[:3]) in ZERO_COST_SUBCOMMANDS:
            return 0.0
        if argv[:2] == ["kubectl", "exec"]:
            return -0.02
        if argv[:2] == ["kubectl", "apply"] or argv[:2] == ["kubectl", "set"]:
            return -0.05
        if argv[:2] == ["kubectl", "edit"]:
            return -0.08
        if argv[:3] == ["kubectl", "rollout", "restart"]:
            return -0.10
        if argv[:2] == ["kubectl", "scale"]:
            return -0.15
        if argv[:2] == ["kubectl", "delete"]:
            return -0.30
        return 0.0

    def is_mutating(self, command: str) -> bool:
        cost = self.action_cost(command)
        return cost < 0.0

    def execute_action(self, command: str, timeout: float = 20.0) -> CommandResult:
        ok, reason = self.validate_action(command)
        if not ok:
            return CommandResult(
                command=command,
                return_code=2,
                stdout="",
                stderr=reason,
                rejected=True,
                action_cost=-0.05,
            )

        argv = shlex.split(command)
        result = self.run_argv(argv, timeout=timeout)
        result.action_cost = self.action_cost(command)
        return result
