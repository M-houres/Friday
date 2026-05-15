"""智能重试 —— 指数退避 + 随机抖动，按错误类型区分策略"""

import asyncio
import logging
import random
from functools import wraps
from typing import Callable, Awaitable

from src.config import settings

logger = logging.getLogger(__name__)


class RetryPolicy:
    """重试策略"""

    def __init__(
        self,
        max_retries: int = None,
        base_delay_s: float = 1.0,
        max_delay_s: float = 64.0,
        jitter: bool = True,
        retryable_errors: set[int] | None = None,
        non_retryable_errors: set[int] | None = None,
    ):
        self.max_retries = max_retries or settings.default_max_retries
        self.base_delay_s = base_delay_s
        self.max_delay_s = max_delay_s
        self.jitter = jitter
        self.retryable_errors = retryable_errors or {429, 500, 502, 503, 504}
        self.non_retryable_errors = non_retryable_errors or {400, 401, 402, 403, 404}

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay_s)
        delay = min(self.base_delay_s * (2 ** attempt), self.max_delay_s)
        if self.jitter:
            delay = random.uniform(0, delay)  # full jitter
        return delay

    def is_retryable(self, status_code: int | None, error_type: str | None = None) -> bool:
        if status_code is not None:
            if status_code in self.non_retryable_errors:
                return False
            if status_code in self.retryable_errors:
                return True
            if 500 <= status_code < 600:
                return True
        if error_type == "timeout":
            return True
        if error_type == "connection_refused":
            return True
        return False


async def with_retry(
    fn: Callable[..., Awaitable],
    *args,
    policy: RetryPolicy | None = None,
    **kwargs,
):
    """带智能重试的异步调用包装器"""
    _policy = policy or RetryPolicy()
    last_error = None

    for attempt in range(_policy.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            last_error = e
            status_code = getattr(e, "status_code", None)
            status = getattr(e, "status", None)
            code = status_code or status
            error_type = type(e).__name__

            if not _policy.is_retryable(code, error_type):
                raise

            if attempt >= _policy.max_retries:
                logger.error(f"Max retries ({_policy.max_retries}) exceeded: {e}")
                raise

            retry_after = None
            if hasattr(e, "response") and hasattr(e.response, "headers"):
                retry_after = e.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        retry_after = float(retry_after)
                    except ValueError:
                        retry_after = None

            delay = _policy.delay(attempt, retry_after)
            logger.warning(
                f"Retry {attempt + 1}/{_policy.max_retries} "
                f"after {delay:.1f}s — {error_type}: {e}"
            )
            await asyncio.sleep(delay)

    raise last_error
