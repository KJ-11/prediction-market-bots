"""Centralized retry decorator for HTTP clients."""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (401, 429, 500, 502, 503, 504)
    return isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout))


def http_retry(name: str = "HTTP"):
    """Tenacity retry decorator for HTTP calls with exponential backoff."""
    return retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        before_sleep=lambda rs: logger.warning(
            "%s retry %d: %s", name, rs.attempt_number, rs.outcome.exception()
        ),
    )
