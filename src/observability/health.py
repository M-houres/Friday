"""健康检查 + 自愈 —— 多层健康探测 + 自动恢复"""

import asyncio
import logging
import time
from enum import Enum
from typing import Awaitable, Callable

from src.db import check_db, check_redis
from src.models.router import model_router

logger = logging.getLogger(__name__)


class HealthLevel(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthChecker:
    """多层健康检查器"""

    def __init__(self):
        self._checks: dict[str, Callable[[], Awaitable[bool]]] = {}
        self._status: dict[str, HealthLevel] = {}
        self._last_check: dict[str, float] = {}
        self._register_builtin()

    def _register_builtin(self):
        self._checks["database"] = check_db
        self._checks["redis"] = check_redis
        self._checks["deepseek"] = lambda: self._check_provider("deepseek")
        self._checks["openai"] = lambda: self._check_provider("openai")
        self._checks["anthropic"] = lambda: self._check_provider("anthropic")

    async def _check_provider(self, name: str) -> bool:
        """检查模型提供者是否可用（简化：查熔断器状态）"""
        try:
            states = model_router.get_circuit_states()
            for s in states:
                if s["provider"] == name and s["state"] == "open":
                    return False
            return True
        except Exception:
            return False

    async def run_all(self) -> dict:
        """运行全部健康检查"""
        results = {}
        for name, check_fn in self._checks.items():
            try:
                ok = await check_fn()
                results[name] = "healthy" if ok else "unhealthy"
            except Exception as e:
                logger.warning(f"Health check {name} failed: {e}")
                results[name] = "unhealthy"

        self._last_check["all"] = time.time()
        return results

    async def get_overall(self) -> HealthLevel:
        results = await self.run_all()
        unhealthy = [k for k, v in results.items() if v == "unhealthy"]

        if not unhealthy:
            return HealthLevel.HEALTHY
        # 数据库或 Redis 挂了 = 不健康，其他 = 降级
        if "database" in unhealthy or "redis" in unhealthy:
            return HealthLevel.UNHEALTHY
        return HealthLevel.DEGRADED


class SelfHealer:
    """自愈器 —— 自动检测和修复常见问题"""

    def __init__(self, health: HealthChecker, interval_s: int = 30):
        self.health = health
        self.interval_s = interval_s
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._heal_loop())
        logger.info(f"SelfHealer started (interval={self.interval_s}s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("SelfHealer stopped")

    async def _heal_loop(self):
        while self._running:
            try:
                await self._heal()
            except Exception as e:
                logger.error(f"SelfHealer error: {e}")
            await asyncio.sleep(self.interval_s)

    async def _heal(self):
        """自愈逻辑"""
        results = await self.health.run_all()

        for name, status in results.items():
            if status == "unhealthy":
                logger.warning(f"Component {name} unhealthy, attempting recovery...")

                if name == "database":
                    await self._reconnect_db()

                elif name == "redis":
                    await self._reconnect_redis()

    async def _reconnect_db(self):
        """尝试重连数据库"""
        try:
            from src.db import engine
            # 尝试重连
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database reconnected")
        except Exception as e:
            logger.error(f"Database reconnect failed: {e}")

    async def _reconnect_redis(self):
        """尝试重连 Redis"""
        try:
            import src.db as db_module
            from src.db import get_redis

            if db_module.redis_client is not None:
                await db_module.redis_client.aclose()
            db_module.redis_client = None  # 强制重建连接
            r = await get_redis()
            await r.ping()
            logger.info("Redis reconnected")
        except Exception as e:
            logger.error(f"Redis reconnect failed: {e}")


# 全局实例
health_checker = HealthChecker()
self_healer = SelfHealer(health_checker)


# 需要从 sqlalchemy 导入 text 用于 _reconnect_db
from sqlalchemy import text
