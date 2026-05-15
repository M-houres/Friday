"""熔断器 —— 三态模式，每模型独立"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from enum import Enum

from src.config import settings

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


TRIP_ERRORS = {429, 500, 502, 503, 504, "timeout", "connection_refused"}
NO_TRIP_ERRORS = {400, 401, 402, 403, 404}


@dataclass
class CircuitConfig:
    failure_threshold: int = 5
    failure_window_s: int = 60
    error_rate_threshold: float = 0.5
    open_timeout_s: int = 30
    half_open_max_requests: int = 3
    success_threshold: int = 2


class CircuitBreaker:
    """熔断器：每 provider + model 一个实例"""

    def __init__(self, provider: str, model: str, config: CircuitConfig | None = None):
        self.provider = provider
        self.model = model
        self.config = config or CircuitConfig(
            failure_threshold=settings.circuit_breaker_failure_threshold,
            open_timeout_s=settings.circuit_breaker_open_timeout_s,
            half_open_max_requests=settings.circuit_breaker_half_open_max,
        )
        self._state = CircuitState.CLOSED
        self._failure_timestamps: list[float] = []
        self._open_until: float = 0
        self._half_open_count: int = 0
        self._half_open_successes: int = 0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        self._check_open_timeout()
        return self._state

    def _check_open_timeout(self):
        if self._state == CircuitState.OPEN and time.time() >= self._open_until:
            self._state = CircuitState.HALF_OPEN
            self._half_open_count = 0
            self._half_open_successes = 0
            logger.info(f"Circuit {self.provider}/{self.model} → HALF_OPEN")

    async def before_call(self) -> bool:
        """调用前检查，返回 True = 允许调用"""
        async with self._lock:
            self._check_open_timeout()
            if self._state == CircuitState.OPEN:
                remaining = self._open_until - time.time()
                logger.warning(
                    f"Circuit {self.provider}/{self.model} OPEN — rejected "
                    f"(reopens in {remaining:.0f}s)"
                )
                return False
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_count >= self.config.half_open_max_requests:
                    return False
                self._half_open_count += 1
            return True

    async def on_success(self):
        """调用成功后上报"""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_successes += 1
                if self._half_open_successes >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_timestamps.clear()
                    logger.info(f"Circuit {self.provider}/{self.model} → CLOSED (recovered)")
            elif self._state == CircuitState.CLOSED:
                self._failure_timestamps.clear()

    async def on_failure(self, error_type: str = ""):
        """调用失败后上报"""
        if self._should_trip(error_type):
            async with self._lock:
                now = time.time()
                self._failure_timestamps.append(now)
                cutoff = now - self.config.failure_window_s
                self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]

                consecutive = len(self._failure_timestamps)
                should_trip = consecutive >= self.config.failure_threshold

                if should_trip:
                    backoff = min(
                        self.config.open_timeout_s * (2 ** (consecutive - self.config.failure_threshold)),
                        300,
                    )
                    self._state = CircuitState.OPEN
                    self._open_until = time.time() + backoff
                    logger.warning(
                        f"Circuit {self.provider}/{self.model} → OPEN "
                        f"(failures={consecutive}, backoff={backoff:.0f}s)"
                    )

    def _should_trip(self, error_type: str) -> bool:
        if not error_type:
            return True
        try:
            code = int(error_type)
            if code in NO_TRIP_ERRORS:
                return False
            if code in TRIP_ERRORS:
                return True
        except (ValueError, TypeError):
            pass
        return error_type in TRIP_ERRORS or error_type not in ("bad_request", "auth_error")

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "state": self.state.value,
            "failure_count": len(self._failure_timestamps),
            "open_until": self._open_until if self.state == CircuitState.OPEN else None,
        }


class CircuitBreakerRegistry:
    """全局熔断器注册中心"""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}

    def _key(self, provider: str, model: str) -> str:
        return f"{provider}:{model}"

    def get(self, provider: str, model: str) -> CircuitBreaker:
        key = self._key(provider, model)
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(provider, model)
        return self._breakers[key]

    def all_states(self) -> list[dict]:
        return [b.to_dict() for b in self._breakers.values()]


breaker_registry = CircuitBreakerRegistry()
