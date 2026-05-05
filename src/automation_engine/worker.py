from __future__ import annotations

import signal
import threading
import time
from multiprocessing import get_context
from multiprocessing.connection import Connection
from typing import Any

from redis.exceptions import RedisError

from automation_engine.api import ExecutionEngine
from automation_engine.database import JobNotFoundError
from automation_engine.logging import get_logger
from automation_engine.models import JobRecord, JobStatus, utc_now
from automation_engine.queue import QueueMessage
from automation_engine.registry import run_registered_task
from automation_engine.retry import calculate_retry_delay_seconds


class JobTimeoutError(TimeoutError):
    pass


def _handler_process_entry(
    registry: object,
    task_name: str,
    payload: dict[str, Any],
    connection: Connection,
) -> None:
    try:
        result = run_registered_task(registry, task_name, payload)  # type: ignore[arg-type]
    except BaseException as exc:
        connection.send(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
    else:
        connection.send({"ok": True, "result": result})
    finally:
        connection.close()


class TaskExecutionError(RuntimeError):
    def __init__(self, error_type: str, error_message: str) -> None:
        super().__init__(error_message)
        self.error_type = error_type


class Worker:
    def __init__(self, engine: ExecutionEngine) -> None:
        self.engine = engine
        self.settings = engine.settings
        self._stop_event = threading.Event()
        self._logger = get_logger(__name__)

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        self._logger.info(
            "shutdown signal received",
            extra={"event": "shutdown", "worker_id": self.settings.worker_id, "status": signum},
        )
        self.stop()

    def stop(self) -> None:
        self._stop_event.set()

    def run_forever(self) -> None:
        self.engine.open()
        self._logger.info(
            "worker started",
            extra={"event": "start", "worker_id": self.settings.worker_id},
        )
        try:
            while not self._stop_event.is_set():
                self._recover_expired_jobs()
                self._wake_due_jobs()
                messages = self._read_messages()
                for message in messages:
                    if self._stop_event.is_set():
                        break
                    self._process_message(message)
        finally:
            self.engine.close()
            self._logger.info(
                "worker stopped",
                extra={"event": "shutdown", "worker_id": self.settings.worker_id},
            )

    def _read_messages(self) -> list[QueueMessage]:
        try:
            stale = self.engine.queue.claim_stale()
            if stale:
                for message in stale:
                    self._logger.info(
                        "claimed stale queue message",
                        extra={
                            "event": "claim",
                            "worker_id": self.settings.worker_id,
                            "job_id": str(message.envelope.job_id),
                        },
                    )
                return stale
            return self.engine.queue.read()
        except (RedisError, ValueError) as exc:
            self._logger.error(
                "queue read failed",
                extra={
                    "event": "dequeue",
                    "worker_id": self.settings.worker_id,
                    "error_type": type(exc).__name__,
                },
            )
            time.sleep(1)
            return []

    def _process_message(self, message: QueueMessage) -> None:
        job_id = message.envelope.job_id
        start = time.monotonic()
        try:
            job = self.engine.store.get_job(job_id)
        except JobNotFoundError:
            self.engine.queue.ack(message.message_id)
            return

        if job.is_terminal or job.status != JobStatus.PENDING or job.next_run_at > utc_now():
            self.engine.queue.ack(message.message_id)
            return

        acquired = self.engine.store.acquire_job(
            job.id, self.settings.worker_id, self.settings.lease_timeout_seconds
        )
        if acquired is None:
            self.engine.queue.ack(message.message_id)
            return

        running_job, execution = acquired
        self._logger.info(
            "job execution started",
            extra={
                "event": "start",
                "job_id": str(running_job.id),
                "execution_id": str(execution.id),
                "task_name": running_job.task_name,
                "worker_id": self.settings.worker_id,
                "attempt": running_job.attempts,
                "status": running_job.status.value,
            },
        )

        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(execution.id, running_job, heartbeat_stop),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self._run_handler_with_timeout(running_job)
        except Exception as exc:
            timed_out = isinstance(exc, JobTimeoutError)
            error_type = getattr(exc, "error_type", type(exc).__name__)
            retry_delay = calculate_retry_delay_seconds(
                running_job.attempts,
                self.settings.retry_base_delay_seconds,
                self.settings.retry_max_delay_seconds,
            )
            updated = self.engine.store.mark_failure(
                running_job.id,
                execution.id,
                str(error_type),
                str(exc),
                retry_delay,
                self.settings.max_attempts,
                timed_out=timed_out,
            )
            if updated.status == JobStatus.PENDING:
                self.engine.queue.publish_job(updated.id)
            self._logger.error(
                "job execution failed",
                extra={
                    "event": "timeout" if timed_out else "retry" if updated.status == JobStatus.PENDING else "fail",
                    "job_id": str(updated.id),
                    "execution_id": str(execution.id),
                    "task_name": updated.task_name,
                    "worker_id": self.settings.worker_id,
                    "attempt": updated.attempts,
                    "status": updated.status.value,
                    "error_type": str(error_type),
                    "duration_ms": int((time.monotonic() - start) * 1000),
                },
            )
        else:
            updated = self.engine.store.mark_success(running_job.id, execution.id, result)
            self._logger.info(
                "job execution succeeded",
                extra={
                    "event": "success",
                    "job_id": str(updated.id),
                    "execution_id": str(execution.id),
                    "task_name": updated.task_name,
                    "worker_id": self.settings.worker_id,
                    "attempt": updated.attempts,
                    "status": updated.status.value,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                },
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=self.settings.heartbeat_interval_seconds + 1)
            self.engine.queue.ack(message.message_id)

    def _run_handler_with_timeout(self, job: JobRecord) -> Any:
        parent_conn, child_conn = get_context("spawn").Pipe(duplex=False)
        process = get_context("spawn").Process(
            target=_handler_process_entry,
            args=(self.engine.registry, job.task_name, job.payload, child_conn),
            daemon=True,
        )
        process.start()
        child_conn.close()
        process.join(timeout=job.timeout_seconds)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            parent_conn.close()
            raise JobTimeoutError(f"Job timed out after {job.timeout_seconds} seconds")
        if process.exitcode not in (0, None):
            parent_conn.close()
            raise TaskExecutionError("TaskProcessCrashed", f"Task process exited with {process.exitcode}")
        if not parent_conn.poll():
            parent_conn.close()
            raise TaskExecutionError("TaskProcessNoResult", "Task process exited without returning a result")
        try:
            response = parent_conn.recv()
        except EOFError as exc:
            parent_conn.close()
            raise TaskExecutionError("TaskProcessNoResult", "Task process closed without a result") from exc
        parent_conn.close()
        if not response.get("ok"):
            raise TaskExecutionError(
                str(response.get("error_type", "TaskExecutionError")),
                str(response.get("error_message", "Task failed")),
            )
        return response.get("result")

    def _heartbeat_loop(
        self, execution_id: object, job: JobRecord, stop_event: threading.Event
    ) -> None:
        while not stop_event.wait(self.settings.heartbeat_interval_seconds):
            try:
                updated = self.engine.store.heartbeat(
                    execution_id, self.settings.lease_timeout_seconds  # type: ignore[arg-type]
                )
                self._logger.info(
                    "job heartbeat",
                    extra={
                        "event": "heartbeat",
                        "job_id": str(job.id),
                        "execution_id": str(execution_id),
                        "task_name": job.task_name,
                        "worker_id": self.settings.worker_id,
                        "attempt": job.attempts,
                        "status": "running" if updated else "lost",
                    },
                )
            except Exception as exc:
                self._logger.error(
                    "heartbeat failed",
                    extra={
                        "event": "heartbeat",
                        "job_id": str(job.id),
                        "execution_id": str(execution_id),
                        "task_name": job.task_name,
                        "worker_id": self.settings.worker_id,
                        "attempt": job.attempts,
                        "status": "error",
                        "error_type": type(exc).__name__,
                    },
                )

    def _wake_due_jobs(self) -> None:
        try:
            for job in self.engine.store.ready_jobs_due_for_wakeup():
                self.engine.queue.publish_job(job.id)
        except Exception as exc:
            self._logger.error(
                "failed to wake due jobs",
                extra={
                    "event": "dequeue",
                    "worker_id": self.settings.worker_id,
                    "error_type": type(exc).__name__,
                },
            )

    def _recover_expired_jobs(self) -> None:
        try:
            for job in self.engine.store.reset_expired_running_jobs():
                self.engine.queue.publish_job(job.id)
        except Exception as exc:
            self._logger.error(
                "failed to recover expired jobs",
                extra={
                    "event": "claim",
                    "worker_id": self.settings.worker_id,
                    "error_type": type(exc).__name__,
                },
            )
