from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


LOGGER = logging.getLogger("workers.runtime")


class ErrorClassification(StrEnum):
    RETRYABLE = "RETRYABLE"
    FATAL = "FATAL"


class RetryStrategy(StrEnum):
    EXPONENTIAL_BACKOFF = "exponential_backoff"


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    delay_seconds: int
    classification: ErrorClassification = ErrorClassification.RETRYABLE
    retry_strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    next_retry_at: datetime | None = None
    reason: str = "retry scheduled"


class RetryHandler:
    def __init__(
        self,
        max_retries: int = 3,
        base_delay_seconds: int = 5,
        max_delay_seconds: int = 300,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")
        if max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be >= 0")
        self.max_retries = max_retries
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.retry_strategy = RetryStrategy.EXPONENTIAL_BACKOFF

    def decide(
        self,
        retry_count: int,
        error_type: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> RetryDecision:
        if retry_count < 0:
            raise ValueError("retry_count must be >= 0")
        current_time = now or datetime.now(UTC)
        classification = classify_error(error_type, error_message)
        next_retry_count = retry_count + 1
        if classification == ErrorClassification.FATAL:
            decision = RetryDecision(
                should_retry=False,
                delay_seconds=0,
                classification=classification,
                retry_strategy=self.retry_strategy,
                next_retry_at=None,
                reason="fatal error",
            )
            self._log_decision(retry_count, error_type, decision)
            return decision
        if retry_count >= self.max_retries:
            decision = RetryDecision(
                should_retry=False,
                delay_seconds=0,
                classification=classification,
                retry_strategy=self.retry_strategy,
                next_retry_at=None,
                reason="max retries reached",
            )
            self._log_decision(retry_count, error_type, decision)
            return decision
        if self.base_delay_seconds == 0 or self.max_delay_seconds == 0:
            decision = RetryDecision(
                should_retry=True,
                delay_seconds=0,
                classification=classification,
                retry_strategy=self.retry_strategy,
                next_retry_at=current_time,
                reason="retry scheduled",
            )
            self._log_decision(retry_count, error_type, decision)
            return decision
        delay = self.base_delay_seconds * (2 ** retry_count)
        capped_delay = min(delay, self.max_delay_seconds)
        decision = RetryDecision(
            should_retry=True,
            delay_seconds=capped_delay,
            classification=classification,
            retry_strategy=self.retry_strategy,
            next_retry_at=current_time + timedelta(seconds=capped_delay),
            reason="retry scheduled",
        )
        self._log_decision(retry_count, error_type, decision)
        return decision

    @staticmethod
    def _log_decision(
        retry_count: int,
        error_type: str | None,
        decision: RetryDecision,
    ) -> None:
        LOGGER.info(
            "retry decision",
            extra={
                "event": "retry_decision",
                "retry_count": retry_count,
                "error_type": error_type,
                "classification": decision.classification.value,
                "retry_strategy": decision.retry_strategy.value,
                "should_retry": decision.should_retry,
                "delay_seconds": decision.delay_seconds,
                "next_retry_at": decision.next_retry_at.isoformat() if decision.next_retry_at else None,
                "reason": decision.reason,
            },
        )


def classify_error(
    error_type: str | None,
    error_message: str | None = None,
) -> ErrorClassification:
    combined = f"{error_type or ''} {error_message or ''}".lower()
    fatal_markers = (
        "invalid input",
        "invalidinput",
        "validationerror",
        "valueerror",
        "typeerror",
        "banned account",
        "bannedaccount",
        "permission denied",
        "unauthorized",
        "forbidden",
        "authentication",
        "tasktypemismatch",
    )
    retryable_markers = (
        "network",
        "connection",
        "timeout",
        "timedout",
        "temporarily unavailable",
        "rate limit",
        "ratelimit",
        "too many requests",
        "service unavailable",
        "deadlock",
        "lock timeout",
        "redis",
        "postgres",
    )
    if any(marker in combined for marker in fatal_markers):
        return ErrorClassification.FATAL
    if any(marker in combined for marker in retryable_markers):
        return ErrorClassification.RETRYABLE
    return ErrorClassification.RETRYABLE
