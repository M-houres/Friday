"""幂等执行 —— 键生成、缓存检查、结果存储"""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class IdempotencyGuard:
    """幂等执行守卫 —— 基于 Redis 的幂等键管理"""

    def __init__(self, ttl_s: int = 86400):  # 默认 24 小时
        self.ttl_s = ttl_s

    @staticmethod
    def generate_key(workflow_id: str, step_id: str, tool_name: str, arguments: dict) -> str:
        """生成幂等键"""
        payload = f"{workflow_id}:{step_id}:{tool_name}:{json.dumps(arguments, sort_keys=True)}"
        return hmac.new(
            key=workflow_id.encode(),
            msg=payload.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

    async def check(self, key: str) -> Any | None:
        """检查是否已执行"""
        try:
            from src.db import get_redis
            r = await get_redis()
            val = await r.get(f"ik:{key}")
            if val and val.startswith("completed:"):
                logger.debug(f"Idempotent hit for {key[:16]}...")
                return json.loads(val.split(":", 1)[1])
            if val == "in_progress":
                # 等待完成
                for _ in range(60):  # 最多等 30 秒
                    await asyncio.sleep(0.5)
                    val = await r.get(f"ik:{key}")
                    if val and val.startswith("completed:"):
                        return json.loads(val.split(":", 1)[1])
                return None
        except Exception as e:
            logger.warning(f"Idempotency check failed: {e}")
        return None

    async def mark_in_progress(self, key: str):
        """标记为执行中"""
        try:
            from src.db import get_redis
            r = await get_redis()
            await r.set(f"ik:{key}", "in_progress", nx=True, ex=300)
        except Exception as e:
            logger.warning(f"Idempotency mark_in_progress failed: {e}")

    async def store(self, key: str, result: Any):
        """存储执行结果"""
        try:
            from src.db import get_redis
            r = await get_redis()
            result_str = json.dumps(result, ensure_ascii=False)
            await r.set(f"ik:{key}", f"completed:{result_str}", ex=self.ttl_s)
        except Exception as e:
            logger.warning(f"Idempotency store failed: {e}")

    async def clear(self, key: str):
        """清除幂等记录（用于可重试的失败）"""
        try:
            from src.db import get_redis
            r = await get_redis()
            await r.delete(f"ik:{key}")
        except Exception as e:
            logger.warning(f"Idempotency clear failed: {e}")



