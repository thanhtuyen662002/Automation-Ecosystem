import pytest

from automation_engine.retry import calculate_retry_delay_seconds


def test_retry_delay_is_exponential_and_capped() -> None:
    assert calculate_retry_delay_seconds(1, 5, 60) == 5
    assert calculate_retry_delay_seconds(2, 5, 60) == 10
    assert calculate_retry_delay_seconds(6, 5, 60) == 60


def test_retry_delay_allows_zero_base() -> None:
    assert calculate_retry_delay_seconds(3, 0, 60) == 0


def test_retry_delay_rejects_invalid_attempt() -> None:
    with pytest.raises(ValueError):
        calculate_retry_delay_seconds(0, 5, 60)

