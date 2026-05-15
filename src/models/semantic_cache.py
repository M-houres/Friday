"""语义缓存 —— 精确匹配 + 语义匹配 (embedding) 减少重复 LLM 调用"""

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from src.db import get_redis
from src.config import settings

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    response: dict
    confidence: float = 1.0
    query_text: str = ""
    model: str = ""
    hit_count: int = 0
    cached_at: float = field(default_factory=time.time)


class SemanticCache:
    """语义缓存 —— 两层：精确匹配 (Redis) + 语义匹配 (embedding 余弦相似度)"""

    def __init__(self, similarity_threshold: float = None):
        self.threshold = similarity_threshold or settings.memory_similarity_threshold
        self._exact_key_prefix = "cache:exact"
        self._semantic_key_prefix = "cache:semantic"
        self._embedding_cache: dict[str, list[float]] = {}

    async def get_exact(self, messages: list[dict], model: str) -> Optional[CacheEntry]:
        """精确匹配 —— 消息完全相同直接返回"""
        try:
            r = await get_redis()
            key = self._build_exact_key(messages, model)
            val = await r.get(key)
            if val:
                data = json.loads(val)
                await r.hincrby(f"{key}:meta", "hits", 1)
                return CacheEntry(**data)
        except Exception as e:
            logger.debug(f"Exact cache miss: {e}")
        return None

    async def set_exact(self, messages: list[dict], model: str, response: dict, ttl: int = 3600):
        """存储精确匹配缓存"""
        try:
            r = await get_redis()
            key = self._build_exact_key(messages, model)
            entry = CacheEntry(
                response=response,
                query_text=json.dumps(messages[-1:], ensure_ascii=False),
                model=model,
            )
            await r.setex(key, ttl, json.dumps({
                "response": entry.response,
                "confidence": entry.confidence,
                "query_text": entry.query_text,
                "model": entry.model,
                "cached_at": entry.cached_at,
                "hit_count": 0,
            }, ensure_ascii=False))
        except Exception as e:
            logger.debug(f"Exact cache store failed: {e}")

    async def get_semantic(
        self, query: str, system_hash: str, model: str,
        threshold: float | None = None,
    ) -> Optional[CacheEntry]:
        """语义匹配 —— 用 embedding 余弦相似度找最近缓存"""
        threshold = threshold or self.threshold

        try:
            r = await get_redis()
            query_embedding = await self._get_embedding(query)
            if query_embedding is None:
                return await self._fallback_keyword_match(query, model, threshold)

            pattern = f"{self._exact_key_prefix}:{model}:*"
            cursor = 0
            best_score = 0
            best_entry = None

            for _ in range(100):
                cursor, keys = await r.scan(cursor, match=pattern, count=20)
                if not keys:
                    break
                for key in keys:
                    val = await r.get(key)
                    if not val:
                        continue
                    try:
                        data = json.loads(val)
                    except json.JSONDecodeError:
                        continue

                    cached_query = data.get("query_text", "")
                    if not cached_query:
                        continue

                    cached_embedding = await self._get_embedding(cached_query)
                    if cached_embedding is None:
                        # 退化为关键词匹配
                        similarity = self._compute_keyword_similarity(query, cached_query)
                    else:
                        similarity = self._cosine_similarity(query_embedding, cached_embedding)

                    if similarity > best_score:
                        best_score = similarity
                        best_entry = data

                if cursor == 0:
                    break

            if best_entry and best_score >= threshold:
                await r.hincrby(
                    f"{self._build_exact_key_str(model, best_entry.get('query_text', ''))}:meta",
                    "semantic_hits", 1,
                )
                entry = CacheEntry(**best_entry)
                entry.confidence = best_score
                return entry

        except Exception as e:
            logger.debug(f"Semantic cache search error: {e}")
        return None

    async def _get_embedding(self, text: str) -> list[float] | None:
        """获取文本的 embedding 向量"""
        if text in self._embedding_cache:
            return self._embedding_cache[text]

        try:
            from src.models.router import model_router
            embeddings = await model_router.embed(
                texts=[text],
                model=settings.memory_embedding_model,
            )
            if embeddings and embeddings[0]:
                self._embedding_cache[text] = embeddings[0]
                if len(self._embedding_cache) > 500:
                    oldest = next(iter(self._embedding_cache))
                    del self._embedding_cache[oldest]
                return embeddings[0]
        except Exception as e:
            logger.debug(f"Embedding generation failed: {e}")
        return None

    async def _fallback_keyword_match(
        self, query: str, model: str, threshold: float,
    ) -> Optional[CacheEntry]:
        """embedding 不可用时的关键词回退"""
        try:
            r = await get_redis()
            pattern = f"{self._exact_key_prefix}:{model}:*"
            cursor = 0
            best_score = 0
            best_entry = None

            for _ in range(100):
                cursor, keys = await r.scan(cursor, match=pattern, count=20)
                if not keys:
                    break
                for key in keys:
                    val = await r.get(key)
                    if not val:
                        continue
                    try:
                        data = json.loads(val)
                    except json.JSONDecodeError:
                        continue
                    cached_query = data.get("query_text", "")
                    if not cached_query:
                        continue
                    similarity = self._compute_keyword_similarity(query, cached_query)
                    if similarity > best_score:
                        best_score = similarity
                        best_entry = data
                if cursor == 0:
                    break

            if best_entry and best_score >= threshold:
                entry = CacheEntry(**best_entry)
                entry.confidence = best_score
                return entry
        except Exception:
            pass
        return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if len(a) != len(b) or not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _compute_keyword_similarity(text1: str, text2: str) -> float:
        """关键词 Jaccard 相似度 (embedding 不可用时的回退)"""
        if not text1 or not text2:
            return 0.0
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    async def get_combined(
        self, messages: list[dict], model: str, query: str = "", system_hash: str = "",
    ) -> Optional[CacheEntry]:
        """组合缓存查询：先精确，再语义"""
        exact = await self.get_exact(messages, model)
        if exact:
            logger.debug(f"Cache HIT (exact) for {model}")
            return exact

        if query:
            semantic = await self.get_semantic(query, system_hash, model)
            if semantic:
                logger.debug(f"Cache HIT (semantic, {semantic.confidence:.2f}) for {model}")
                return semantic

        return None


semantic_cache = SemanticCache()
