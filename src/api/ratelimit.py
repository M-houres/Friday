"""速率限制中间件 —— 基于 Redis 的令牌桶/滑动窗口"""

import asyncio
import logging
import os
import time
from typing import Callable, Optional

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from src.db import get_redis
from src.config import settings

logger = logging.getLogger(__name__)


SKIP_PREFIXES = ("/api/v1/health", "/api/v1/stream")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 Redis 的滑动窗口速率限制

    支持两种粒度:
    - 全局: 服务级别限流 (GLOBAL_RPM)
    - 用户: 每用户限流 (USER_RPM)
    - IP: 每 IP 限流 (IP_RPM)
    """

    def __init__(self, app, global_rpm: int = 6000, user_rpm: int = 60,
                 ip_rpm: int = 30, window_s: int = 60):
        super().__init__(app)
        self.global_rpm = global_rpm
        self.user_rpm = user_rpm
        self.ip_rpm = ip_rpm
        self.window_s = window_s

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if self._should_skip(path):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        user_id = getattr(request.state, "user_id", None) or "anonymous"
        now = time.time()
        window_key = int(now // self.window_s)

        try:
            r = await get_redis()

            # IP 级别限流
            ip_key = f"ratelimit:ip:{client_ip}:{window_key}"
            if self.ip_rpm > 0:
                count = await r.incr(ip_key)
                if count == 1:
                    await r.expire(ip_key, self.window_s * 2)
                if count > self.ip_rpm:
                    retry_after = self.window_s - (now % self.window_s)
                    raise HTTPException(
                        status_code=429,
                        detail=f"IP rate limit exceeded. Retry after {retry_after:.0f}s",
                        headers={"Retry-After": str(int(retry_after)), "X-RateLimit-Limit": str(self.ip_rpm)},
                    )

            # 用户级别限流
            if self.user_rpm > 0 and user_id != "anonymous":
                user_key = f"ratelimit:user:{user_id}:{window_key}"
                count = await r.incr(user_key)
                if count == 1:
                    await r.expire(user_key, self.window_s * 2)
                if count > self.user_rpm:
                    retry_after = self.window_s - (now % self.window_s)
                    raise HTTPException(
                        status_code=429,
                        detail=f"User rate limit exceeded. Retry after {retry_after:.0f}s",
                        headers={"Retry-After": str(int(retry_after)), "X-RateLimit-Limit": str(self.user_rpm)},
                    )

            # 全局限流
            if self.global_rpm > 0:
                global_key = f"ratelimit:global:{window_key}"
                count = await r.incr(global_key)
                if count == 1:
                    await r.expire(global_key, self.window_s * 2)
                if count > self.global_rpm:
                    retry_after = self.window_s - (now % self.window_s)
                    raise HTTPException(
                        status_code=429,
                        detail=f"Global rate limit exceeded. Retry after {retry_after:.0f}s",
                        headers={"Retry-After": str(int(retry_after))},
                    )

        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Rate limit check failed (allowing request): {e}")

        response = await call_next(request)
        return response

    @staticmethod
    def _is_test_runtime() -> bool:
        return bool(os.getenv("PYTEST_CURRENT_TEST"))

    def _should_skip(self, path: str) -> bool:
        return self._is_test_runtime() or path.startswith(SKIP_PREFIXES)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            return real_ip
        client = request.client
        return client.host if client else "unknown"


class RateLimiter:
    """独立速率限制器 —— 可用于模型调用限流 (RPM)"""

    def __init__(self, rpm: int = 500, window_s: int = 60):
        self.rpm = rpm
        self.window_s = window_s
        self._local_counts: dict[int, int] = {}
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """获取许可，返回 True 表示可以调用"""
        now = int(time.time() // self.window_s)

        async with self._lock:
            # 清理过期窗口
            stale = [k for k in self._local_counts if k < now - 2]
            for k in stale:
                del self._local_counts[k]

            count = self._local_counts.get(now, 0)
            if count >= self.rpm:
                return False
            self._local_counts[now] = count + 1
            return True

    async def wait_if_needed(self):
        """必要时等待"""
        while not await self.acquire():
            await asyncio.sleep(1.0)


# 全局 IP 限流器
rate_limiter = RateLimiter(rpm=settings.rate_limit_deepseek_rpm)


def create_rate_limit_middleware() -> RateLimitMiddleware:
    """根据配置创建限流中间件"""
    return RateLimitMiddleware(
        global_rpm=getattr(settings, "rate_limit_global_rpm", 6000),
        user_rpm=getattr(settings, "rate_limit_user_rpm", 60),
        ip_rpm=getattr(settings, "rate_limit_ip_rpm", 30),
    )
