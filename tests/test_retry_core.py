"""Unit tests for retry / state-machine core logic.

These tests run entirely in-process — no database or network required.
"""
from __future__ import annotations

import unittest

from database.database import (
    InvalidStateTransition,
    RetryConfig,
    TaskStatus,
    VALID_TASK_TRANSITIONS,
    _ensure_task_transition,
)


class TestRetryConfigDelayForAttempt(unittest.TestCase):
    """RetryConfig.delay_for_attempt() — exponential back-off."""

    def _config(self, base: int = 5, max_delay: int = 300) -> RetryConfig:
        return RetryConfig(base_delay_seconds=base, max_delay_seconds=max_delay)

    def test_first_attempt_returns_base_delay(self) -> None:
        cfg = self._config(base=5)
        self.assertEqual(cfg.delay_for_attempt(0), 5)

    def test_second_attempt_doubles(self) -> None:
        cfg = self._config(base=5)
        # retry_count=1 → 5 * 2^0 = 5, retry_count=2 → 5 * 2^1 = 10
        self.assertEqual(cfg.delay_for_attempt(2), 10)

    def test_delay_capped_at_max(self) -> None:
        cfg = self._config(base=60, max_delay=300)
        # 60 * 2^5 = 1920 → capped at 300
        self.assertEqual(cfg.delay_for_attempt(6), 300)

    def test_zero_base_always_returns_zero(self) -> None:
        cfg = self._config(base=0, max_delay=300)
        self.assertEqual(cfg.delay_for_attempt(5), 0)

    def test_zero_max_always_returns_zero(self) -> None:
        cfg = self._config(base=5, max_delay=0)
        self.assertEqual(cfg.delay_for_attempt(1), 0)

    def test_negative_retry_count_raises(self) -> None:
        cfg = self._config()
        with self.assertRaises(ValueError):
            cfg.delay_for_attempt(-1)


class TestValidTaskTransitions(unittest.TestCase):
    """VALID_TASK_TRANSITIONS covers all expected edges."""

    def test_pending_can_become_ready(self) -> None:
        self.assertIn(TaskStatus.READY, VALID_TASK_TRANSITIONS[TaskStatus.PENDING])

    def test_ready_can_become_running(self) -> None:
        self.assertIn(TaskStatus.RUNNING, VALID_TASK_TRANSITIONS[TaskStatus.READY])

    def test_running_can_succeed(self) -> None:
        self.assertIn(TaskStatus.SUCCESS, VALID_TASK_TRANSITIONS[TaskStatus.RUNNING])

    def test_running_can_retry(self) -> None:
        self.assertIn(TaskStatus.RETRY, VALID_TASK_TRANSITIONS[TaskStatus.RUNNING])

    def test_running_can_fail(self) -> None:
        self.assertIn(TaskStatus.FAILED, VALID_TASK_TRANSITIONS[TaskStatus.RUNNING])

    def test_success_has_no_transitions(self) -> None:
        self.assertEqual(len(VALID_TASK_TRANSITIONS[TaskStatus.SUCCESS]), 0)

    def test_failed_can_be_reset_to_pending(self) -> None:
        self.assertIn(TaskStatus.PENDING, VALID_TASK_TRANSITIONS[TaskStatus.FAILED])


class TestEnsureTaskTransition(unittest.TestCase):
    """_ensure_task_transition() raises on illegal moves."""

    def test_legal_transition_does_not_raise(self) -> None:
        # Should be a no-op
        _ensure_task_transition(TaskStatus.PENDING, TaskStatus.READY)

    def test_illegal_transition_raises_invalid_state_transition(self) -> None:
        with self.assertRaises(InvalidStateTransition):
            _ensure_task_transition(TaskStatus.SUCCESS, TaskStatus.RUNNING)

    def test_success_to_failed_raises(self) -> None:
        with self.assertRaises(InvalidStateTransition):
            _ensure_task_transition(TaskStatus.SUCCESS, TaskStatus.FAILED)

    def test_pending_to_running_raises(self) -> None:
        # Must go PENDING → READY → RUNNING
        with self.assertRaises(InvalidStateTransition):
            _ensure_task_transition(TaskStatus.PENDING, TaskStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
