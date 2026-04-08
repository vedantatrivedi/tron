from __future__ import annotations

"""OpenEnv-compatible server adapter for the tron benchmark core."""

import logging
import os
import time
from pathlib import Path
from threading import Lock, Thread
from uuid import uuid4

logger = logging.getLogger("tron.server")


class ClusterNotAvailableError(RuntimeError):
    """Raised when the Kubernetes cluster is not reachable."""


from tron.env import TronEnvironment
from tron.models import BenchmarkConfig, ClusterConfig, ObservationBundle, StepTransition
from tron_openenv.models import (
    ClusterSummaryView,
    ResetRequest,
    ResetResponse,
    ServiceProbeView,
    StepResponse,
    TronAction,
    TronObservation,
    TronReward,
    TronState,
    TronTask,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_CONFIG = BenchmarkConfig()

TASK_SCENARIO_IDS: dict[str, str] = {
    "easy": "service-selector-mismatch",
    "medium": "bad-rollout-wrong-redis-host",
    "hard": "networkpolicy-plus-secondary-drift",
}

TASKS: dict[str, TronTask] = {
    "easy": TronTask(
        id="easy",
        difficulty="easy",
        default_seed=11,
        max_agent_steps=12,
    ),
    "medium": TronTask(
        id="medium",
        difficulty="medium",
        default_seed=13,
        max_agent_steps=15,
    ),
    "hard": TronTask(
        id="hard",
        difficulty="hard",
        default_seed=17,
        max_agent_steps=18,
    ),
}


def _build_cluster_config() -> ClusterConfig:
    ingress_host_header = os.getenv("INGRESS_HOST_HEADER", "tron.localhost")
    ingress_endpoint_host = os.getenv("INGRESS_URL_HOST") or os.getenv("INGRESS_HOST")
    if ingress_endpoint_host in {"", "tron.localhost"}:
        ingress_endpoint_host = None

    return ClusterConfig(
        cluster_name=os.getenv("TRON_CLUSTER_NAME", "tron-lab"),
        namespace=os.getenv("TRON_NAMESPACE", "tron"),
        kubeconfig_path=os.getenv("KUBECONFIG"),
        ingress_host=ingress_host_header,
        ingress_port=int(os.getenv("INGRESS_PORT", "8080")),
        ingress_url_host=ingress_endpoint_host,
    )


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default %.3f", name, raw, default)
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using default %d", name, raw, default)
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _build_config(max_agent_steps: int) -> BenchmarkConfig:
    return BenchmarkConfig(
        random_seed=0,
        max_agent_steps=max_agent_steps,
        blackbox_timeout_seconds=_float_env(
            "TRON_OPENENV_BLACKBOX_TIMEOUT_SECONDS",
            DEFAULT_BENCHMARK_CONFIG.blackbox_timeout_seconds,
        ),
        trusted_timeout_seconds=_float_env(
            "TRON_OPENENV_TRUSTED_TIMEOUT_SECONDS",
            DEFAULT_BENCHMARK_CONFIG.trusted_timeout_seconds,
        ),
        rollout_status_timeout_seconds=_int_env(
            "TRON_OPENENV_ROLLOUT_TIMEOUT_SECONDS",
            DEFAULT_BENCHMARK_CONFIG.rollout_status_timeout_seconds,
        ),
        mutation_settle_seconds=_float_env(
            "TRON_OPENENV_MUTATION_SETTLE_SECONDS",
            DEFAULT_BENCHMARK_CONFIG.mutation_settle_seconds,
        ),
        transient_probe_wait_seconds=_float_env(
            "TRON_OPENENV_TRANSIENT_PROBE_WAIT_SECONDS",
            DEFAULT_BENCHMARK_CONFIG.transient_probe_wait_seconds,
        ),
        skip_reset_validation=_bool_env(
            "TRON_OPENENV_SKIP_RESET_VALIDATION",
            DEFAULT_BENCHMARK_CONFIG.skip_reset_validation,
        ),
        work_dir=ROOT,
        cluster=_build_cluster_config(),
    )


class TronOpenEnvService:
    def __init__(self, env: TronEnvironment | None = None, tasks: dict[str, TronTask] | None = None) -> None:
        self.tasks = tasks or TASKS
        self.env = env or TronEnvironment(_build_config(self.tasks["easy"].max_agent_steps))
        self.lock = Lock()
        self.jobs_lock = Lock()
        self.cluster_check_timeout_seconds = _float_env("TRON_OPENENV_CLUSTER_CHECK_TIMEOUT_SECONDS", 8.0)
        self.cluster_check_ttl_seconds = _float_env("TRON_OPENENV_CLUSTER_CHECK_TTL_SECONDS", 0.0)
        self._last_cluster_check_monotonic: float | None = None
        self.reset_jobs: dict[str, dict[str, object]] = {}
        self.current_task: TronTask | None = None
        self.current_seed: int | None = None
        self.episode_id: str | None = None
        self.cumulative_reward: float = 0.0
        self.last_evaluation = None

    def list_tasks(self) -> list[TronTask]:
        return [self.tasks[key] for key in ("easy", "medium", "hard")]

    def _require_task(self, task_id: str) -> TronTask:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task_id: {task_id}") from exc

    def _observation_to_model(self, observation: ObservationBundle, done: bool) -> TronObservation:
        if self.current_task is None or self.env.current_instance is None:
            raise RuntimeError("task state is not initialized")
        return TronObservation(
            task_id=self.current_task.id,
            step_count=observation.step_number,
            incident_brief=observation.incident_brief,
            last_action=observation.last_action,
            last_reward=observation.last_reward,
            service_probe=ServiceProbeView(
                health_status=observation.service_probe.health_status,
                data_status=observation.service_probe.data_status,
                http_status=observation.service_probe.http_status,
                latency_ms=observation.service_probe.latency_ms,
                score=observation.service_probe.score,
            ),
            cluster_summary=ClusterSummaryView(
                pods=observation.cluster_summary.pods,
                services=observation.cluster_summary.services,
                deployments=observation.cluster_summary.deployments,
                endpoints=observation.cluster_summary.endpoints,
            ),
            recent_change_hint=observation.recent_change_hint,
            done=done,
            metadata={
                "difficulty": self.current_task.difficulty,
            },
        )

    def _assert_cluster_reachable(self) -> None:
        executor = getattr(self.env, "executor", None)
        if executor is None:
            return
        started = time.perf_counter()
        if self.cluster_check_ttl_seconds > 0 and self._last_cluster_check_monotonic is not None:
            age_seconds = time.monotonic() - self._last_cluster_check_monotonic
            if age_seconds < self.cluster_check_ttl_seconds:
                logger.info(
                    "[reset] cluster precheck cache hit age=%.2fs ttl=%.2fs",
                    age_seconds,
                    self.cluster_check_ttl_seconds,
                )
                return
        request_timeout_seconds = max(int(self.cluster_check_timeout_seconds - 1), 1)
        try:
            result = executor.run_argv(
                ["kubectl", "cluster-info", f"--request-timeout={request_timeout_seconds}s"],
                timeout=self.cluster_check_timeout_seconds,
            )
        except FileNotFoundError:
            logger.warning(
                "[reset] cluster precheck failed after %.2fs: kubectl not found in PATH",
                time.perf_counter() - started,
            )
            raise ClusterNotAvailableError(
                "kubectl not found in PATH. The server is not connected to a Kubernetes cluster. "
                "Provide cluster credentials via the KUBECONFIG_B64 environment variable."
            )
        if result.return_code != 0:
            detail = (result.stderr or result.stdout or "no output").strip().splitlines()[0][:200]
            logger.warning(
                "[reset] cluster precheck failed after %.2fs: %s",
                time.perf_counter() - started,
                detail,
            )
            raise ClusterNotAvailableError(
                f"Kubernetes cluster is not reachable. "
                f"Provide cluster credentials via the KUBECONFIG_B64 environment variable. "
                f"kubectl error: {detail}"
            )
        self._last_cluster_check_monotonic = time.monotonic()
        logger.info(
            "[reset] cluster precheck ok in %.2fs",
            time.perf_counter() - started,
        )

    def reset(self, request: ResetRequest) -> ResetResponse:
        started = time.perf_counter()
        with self.lock:
            task = self._require_task(request.task_id)
            seed = request.seed if request.seed is not None else task.default_seed
            scenario_id = TASK_SCENARIO_IDS[task.id]
            self.env.config.max_agent_steps = task.max_agent_steps
            logger.info(
                "[reset] requested task=%s scenario=%s seed=%d hard_reset=%s",
                task.id, scenario_id, seed, request.hard_reset,
            )
            self._assert_cluster_reachable()
            env_reset_started = time.perf_counter()
            try:
                observation = self.env.reset(
                    scenario_id=scenario_id,
                    seed=seed,
                    hard_reset=request.hard_reset,
                )
            except Exception:
                logger.exception(
                    "[reset] env reset failed after %.2fs for task=%s scenario=%s seed=%d",
                    time.perf_counter() - env_reset_started,
                    task.id,
                    scenario_id,
                    seed,
                )
                raise
            self.current_task = task
            self.current_seed = seed
            self.episode_id = uuid4().hex
            self.cumulative_reward = 0.0
            self.last_evaluation = None
            logger.info(
                "[reset] episode=%s ready score=%.2f health=%s data=%s env_reset=%.2fs total=%.2fs incident=%r",
                self.episode_id[:8],
                observation.service_probe.score,
                observation.service_probe.health_status,
                observation.service_probe.data_status,
                time.perf_counter() - env_reset_started,
                time.perf_counter() - started,
                observation.incident_brief,
            )
            return ResetResponse(
                task=task,
                observation=self._observation_to_model(observation, done=False),
            )

    def start_reset_async(self, request: ResetRequest) -> dict[str, object]:
        task_id = request.task_id
        seed = request.seed
        hard_reset = request.hard_reset
        job_id = uuid4().hex
        with self.jobs_lock:
            self.reset_jobs[job_id] = {
                "status": "running",
                "task_id": task_id,
                "seed": seed,
                "hard_reset": hard_reset,
                "submitted_at": time.time(),
                "elapsed_seconds": 0.0,
                "result": None,
                "error": None,
            }
        worker = Thread(target=self._run_reset_async_job, args=(job_id, request), daemon=True)
        worker.start()
        logger.info("[reset_async] accepted job=%s task=%s seed=%s hard_reset=%s", job_id[:8], task_id, seed, hard_reset)
        return self.get_reset_job(job_id)

    def _run_reset_async_job(self, job_id: str, request: ResetRequest) -> None:
        started = time.perf_counter()
        try:
            response = self.reset(request)
        except Exception as exc:
            elapsed_seconds = time.perf_counter() - started
            with self.jobs_lock:
                job = self.reset_jobs[job_id]
                job["status"] = "failed"
                job["elapsed_seconds"] = round(elapsed_seconds, 3)
                job["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("[reset_async] job=%s failed after %.2fs", job_id[:8], elapsed_seconds)
            return

        elapsed_seconds = time.perf_counter() - started
        with self.jobs_lock:
            job = self.reset_jobs[job_id]
            job["status"] = "completed"
            job["elapsed_seconds"] = round(elapsed_seconds, 3)
            job["result"] = response.model_dump()
        logger.info("[reset_async] job=%s completed in %.2fs", job_id[:8], elapsed_seconds)

    def get_reset_job(self, job_id: str) -> dict[str, object]:
        with self.jobs_lock:
            try:
                payload = self.reset_jobs[job_id].copy()
            except KeyError as exc:
                raise KeyError(f"unknown reset job: {job_id}") from exc
        payload["job_id"] = job_id
        return payload

    def step(self, action: TronAction) -> StepResponse:
        with self.lock:
            if self.current_task is None or self.env.current_instance is None:
                raise RuntimeError("reset() must be called before step()")

            step_num = self.env.step_number + 1
            transition: StepTransition = self.env.step(action.command)
            self.cumulative_reward = round(self.cumulative_reward + transition.reward, 3)
            done = transition.done
            info = dict(transition.info)
            last_step = self.env.steps[-1]
            info.update(
                {
                    "command": last_step.command,
                    "return_code": last_step.return_code,
                    "stdout": last_step.stdout,
                    "stderr": last_step.stderr,
                }
            )

            rejected = info.get("rejected", False)
            timed_out = info.get("timed_out", False)
            detail = ""
            if last_step.return_code != 0 or rejected or timed_out:
                raw = last_step.stderr or last_step.stdout or ""
                detail = " | " + raw.replace("\n", " ")[:120]
            logger.info(
                "[step %d] episode=%s rc=%d reward=%+.2f score=%.2f health=%s data=%s%s%s%s | %s",
                step_num,
                self.episode_id[:8] if self.episode_id else "?",
                last_step.return_code,
                transition.reward,
                transition.service_score,
                transition.observation.service_probe.health_status,
                transition.observation.service_probe.data_status,
                " REJECTED" if rejected else "",
                " TIMEOUT" if timed_out else "",
                detail,
                action.command,
            )

            if transition.done:
                evaluation = self.env.evaluate(self.env.current_instance, self.env.steps)
                self.last_evaluation = evaluation
                info.update(
                    {
                        "oracle_score": evaluation.score,
                        "oracle_verdict": evaluation.verdict.value,
                        "oracle_summary": evaluation.summary,
                        "repair_checks": [check.__dict__ for check in evaluation.checks],
                    }
                )
                if (
                    transition.service_score >= 1.0
                    and evaluation.verdict.value != "success"
                    and self.env.step_number < self.env.config.max_agent_steps
                ):
                    self.env.done = False
                    done = False
                    info["repair_complete"] = False
                    logger.info(
                        "[step %d] repair_incomplete oracle=%.2f continuing",
                        step_num, evaluation.score,
                    )
                else:
                    info["repair_complete"] = evaluation.verdict.value == "success"
                    logger.info(
                        "[episode] episode=%s verdict=%s oracle=%.2f reward=%.3f steps=%d | %s",
                        self.episode_id[:8] if self.episode_id else "?",
                        evaluation.verdict.value,
                        evaluation.score,
                        self.cumulative_reward,
                        self.env.step_number,
                        evaluation.summary,
                    )

            return StepResponse(
                observation=self._observation_to_model(transition.observation, done=done),
                reward=TronReward(value=transition.reward),
                done=done,
                info=info,
            )

    def state(self) -> TronState:
        with self.lock:
            service_score = (
                self.env.current_observation.service_probe.score if self.env.current_observation else None
            )
            return TronState(
                episode_id=self.episode_id,
                task=self.current_task,
                seed=self.current_seed,
                step_count=self.env.step_number,
                cumulative_reward=self.cumulative_reward,
                done=self.env.done,
                last_action=self.env.current_observation.last_action if self.env.current_observation else None,
                last_reward=self.env.last_reward,
                service_score=service_score,
                oracle_score=self.last_evaluation.score if self.last_evaluation else None,
                oracle_verdict=self.last_evaluation.verdict.value if self.last_evaluation else None,
                oracle_summary=self.last_evaluation.summary if self.last_evaluation else None,
            )
