"""记忆库 —— 热温冷三层存储 + 上下文压缩"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_redis
from src.config import settings

logger = logging.getLogger(__name__)


class MemoryStore:
    """记忆库 —— 三层存储: Redis (热) → PG (温) → S3 (冷, 暂未实现)"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.message_limit_hot = settings.session_message_limit_hot
        self.message_limit_context = settings.session_message_limit_context

    # ── 热层 (Redis) ──

    async def add_message(self, session_id: str, message: dict):
        """追加消息到热层"""
        try:
            r = await get_redis()
            key = f"session:{session_id}:messages"
            pipe = r.pipeline()
            pipe.rpush(key, json.dumps(message, ensure_ascii=False))
            pipe.ltrim(key, -self.message_limit_hot, -1)
            pipe.expire(key, 86400)
            await pipe.execute()
        except Exception as e:
            logger.warning(f"Redis add_message failed: {e}")

    async def get_context_messages(self, session_id: str, limit: int | None = None) -> list[dict]:
        """获取上下文消息（最近 N 条）"""
        try:
            r = await get_redis()
            limit = limit or self.message_limit_context
            key = f"session:{session_id}:messages"
            messages = await r.lrange(key, -limit, -1)
            return [json.loads(m) for m in messages]
        except Exception as e:
            logger.warning(f"Redis get_context failed: {e}")
            return []

    async def get_all_messages(self, session_id: str) -> list[dict]:
        try:
            r = await get_redis()
            key = f"session:{session_id}:messages"
            messages = await r.lrange(key, 0, -1)
            return [json.loads(m) for m in messages]
        except Exception as e:
            return []

    # ── 温层 (PostgreSQL) ──

    async def archive_session(self, session_id: str, user_id: str):
        """将热层会话归档到温层"""
        messages = await self.get_all_messages(session_id)
        if not messages:
            return

        # 生成摘要 (简化版: 取前 500 字符)
        all_text = " ".join(m.get("content", "") for m in messages if isinstance(m, dict))
        summary = all_text[:1000] if all_text else "(empty session)"

        await self.db.execute(
            text("""
                INSERT INTO session_memories (session_id, user_id, summary, raw_messages, archived_at, created_at, updated_at)
                VALUES (:session_id, :user_id, :summary, :raw, NOW(), NOW(), NOW())
                ON CONFLICT (session_id) DO UPDATE
                SET summary = :summary, raw_messages = :raw, updated_at = NOW()
            """),
            {
                "session_id": session_id,
                "user_id": user_id,
                "summary": summary,
                "raw": json.dumps(messages, ensure_ascii=False),
            },
        )
        await self.db.commit()

        # 清除热层
        try:
            r = await get_redis()
            await r.delete(f"session:{session_id}:messages")
            await r.delete(f"session:{session_id}:meta")
        except Exception:
            pass

    async def search_memories(self, user_id: str, query: str, limit: int = 10) -> list[dict]:
        """搜索用户的历史记忆（简化版: 文本搜索）"""
        rows = await self.db.execute(
            text("""
                SELECT session_id, summary, created_at
                FROM session_memories
                WHERE user_id = :user_id AND summary ILIKE :pattern
                ORDER BY created_at DESC
                LIMIT :limit
            """),
            {"user_id": user_id, "pattern": f"%{query}%", "limit": limit},
        )
        return [dict(r._mapping) for r in rows.fetchall()]

    # ── 长期记忆 ──

    async def store_long_term(
        self, user_id: str, memory_type: str, content: dict, importance: float = 0.5,
    ):
        """存储长期记忆"""
        await self.db.execute(
            text("""
                INSERT INTO long_term_memories (user_id, type, content, importance, created_at)
                VALUES (:user_id, :type, :content, :importance, NOW())
            """),
            {
                "user_id": user_id, "type": memory_type,
                "content": json.dumps(content, ensure_ascii=False),
                "importance": importance,
            },
        )
        await self.db.commit()

    async def recall_long_term(self, user_id: str, memory_type: str | None = None, limit: int = 10) -> list[dict]:
        """召回长期记忆"""
        query = "SELECT * FROM long_term_memories WHERE user_id = :user_id"
        params = {"user_id": user_id, "limit": limit}
        if memory_type:
            query += " AND type = :type"
            params["type"] = memory_type

        query += " ORDER BY importance DESC, access_count DESC LIMIT :limit"
        rows = await self.db.execute(text(query), params)

        results = []
        for r in rows.fetchall():
            d = dict(r._mapping)
            # 更新访问计数
            await self.db.execute(
                text("UPDATE long_term_memories SET access_count = access_count + 1, last_accessed = NOW() WHERE id = :id"),
                {"id": d["id"]},
            )
            results.append(d)

        await self.db.commit()
        return results


class ContextCompressor:
    """上下文压缩 —— 渐进式窗口管理"""

    @staticmethod
    def saliency_score(message: dict, current_query: str) -> float:
        """计算消息与当前查询的相关性"""
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        if not content or not current_query:
            return 0.5

        # 简单的词重叠分数
        query_words = set(current_query.lower().split())
        content_words = set(content.lower().split())
        overlap = len(query_words & content_words)
        if not query_words:
            return 0.5
        score = overlap / len(query_words)

        # 工具结果加权
        if isinstance(message, dict) and message.get("role") == "tool":
            score *= 1.5

        # 过短的消息降权
        if len(content) < 20:
            score *= 0.3

        return min(score, 1.0)

    @staticmethod
    def compress(
        messages: list[dict],
        current_query: str,
        recent_count: int = 3,
        max_comressed: int = 10,
    ) -> list[dict]:
        """压缩消息列表 —— 保留最近 N 条原文 + 之前的高相关性摘要"""
        if len(messages) <= recent_count + max_comressed:
            return messages

        recent = messages[-recent_count:]
        older = messages[:-recent_count]

        # 按相关性排序，保留 top K
        scored = [(ContextCompressor.saliency_score(m, current_query), m) for m in older]
        scored.sort(key=lambda x: x[0], reverse=True)

        kept = [m for _, m in scored[:max_comressed]]
        # 其余压缩成一条摘要
        rest = [m for _, m in scored[max_comressed:]]
        if rest:
            summary_text = " ".join(
                m.get("content", "")[:100] for m in rest if isinstance(m, dict)
            )[:500]
            if summary_text:
                kept.append({"role": "system", "content": f"[对话历史摘要] {summary_text}"})

        kept.sort(key=lambda m: messages.index(m) if m in messages else 999999)
        return kept + recent
