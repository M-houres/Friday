"""增强记忆 —— 语义/经历/程序 三层记忆 (LangGraph 架构)"""

import json
import logging
import time
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)


class SemanticMemory:
    """语义记忆 —— 跨会话记住用户偏好、事实"""
    
    def __init__(self):
        self._facts: dict[str, dict] = {}  # user_id → {key: {value, confidence, updated_at}}
    
    def remember(self, user_id: str, key: str, value: Any, confidence: float = 0.8):
        if user_id not in self._facts:
            self._facts[user_id] = {}
        self._facts[user_id][key] = {
            "value": value,
            "confidence": confidence,
            "updated_at": time.time(),
        }
        logger.debug(f"Semantic memory: {user_id}.{key} = {value}")
    
    def recall(self, user_id: str, key: str = "") -> Any:
        user_facts = self._facts.get(user_id, {})
        if key:
            fact = user_facts.get(key)
            return fact["value"] if fact else None
        return {k: v["value"] for k, v in user_facts.items()}
    
    def forget(self, user_id: str, key: str = ""):
        if key:
            self._facts.get(user_id, {}).pop(key, None)
        else:
            self._facts.pop(user_id, None)


class EpisodicMemory:
    """经历记忆 —— 记住过去成功的执行模式，用于 few-shot"""
    
    def __init__(self, max_episodes: int = 100):
        self.max_episodes = max_episodes
        self._episodes: list[dict] = []
    
    def record(self, task: str, workflow: str, result: dict, success: bool = True):
        episode = {
            "task": task,
            "workflow": workflow,
            "result_summary": str(result)[:500],
            "success": success,
            "timestamp": time.time(),
        }
        self._episodes.append(episode)
        if len(self._episodes) > self.max_episodes:
            self._episodes = self._episodes[-self.max_episodes:]
    
    def recall_similar(self, task: str, limit: int = 3) -> list[dict]:
        """召回相似的成功经历"""
        task_lower = task.lower()
        scored = []
        for ep in self._episodes:
            if not ep["success"]:
                continue
            overlap = len(set(task_lower.split()) & set(ep["task"].lower().split()))
            scored.append((overlap, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:limit]]
    
    def get_recent(self, limit: int = 10) -> list[dict]:
        return self._episodes[-limit:]


class ProceduralMemory:
    """程序记忆 —— Agent 自我总结"这样做更快"，改写自己的策略"""
    
    def __init__(self):
        self._insights: list[dict] = []
    
    def learn(self, insight: str, context: str = "", confidence: float = 0.5):
        self._insights.append({
            "insight": insight,
            "context": context,
            "confidence": confidence,
            "timestamp": time.time(),
        })
        logger.info(f"Procedural memory: learned '{insight[:80]}...'")
    
    def get_insights(self, min_confidence: float = 0.3) -> list[str]:
        return [i["insight"] for i in self._insights if i["confidence"] >= min_confidence]
    
    def best_practices(self, limit: int = 5) -> list[str]:
        sorted_insights = sorted(self._insights, key=lambda i: i["confidence"], reverse=True)
        return [i["insight"] for i in sorted_insights[:limit]]


class EnhancedMemoryStore:
    """增强记忆库 —— 整合三层记忆"""
    
    def __init__(self):
        self.semantic = SemanticMemory()
        self.episodic = EpisodicMemory()
        self.procedural = ProceduralMemory()
    
    def build_context(self, user_id: str, task: str) -> dict:
        """构建完整上下文给 Agent"""
        context = {}
        
        # 语义记忆: 用户偏好
        user_prefs = self.semantic.recall(user_id)
        if user_prefs:
            context["user_preferences"] = user_prefs
        
        # 经历记忆: 相似任务经验
        similar = self.episodic.recall_similar(task, limit=3)
        if similar:
            context["past_experiences"] = [
                {"task": ep["task"], "approach": ep["workflow"]}
                for ep in similar
            ]
        
        # 程序记忆: 最佳实践
        practices = self.procedural.best_practices(limit=3)
        if practices:
            context["best_practices"] = practices
        
        return context
    
    def learn_from_task(self, task: str, workflow: str, result: dict, success: bool):
        """从一次任务执行中学习"""
        if success:
            self.episodic.record(task, workflow, result, success)
            # 如果用了特定工具且成功，记录为最佳实践
            self.procedural.learn(
                insight=f"任务 '{task[:50]}' 使用工作流 '{workflow[:50]}' 成功完成",
                confidence=0.6,
            )
    
    def to_dict(self) -> dict:
        return {
            "semantic": {"facts_count": sum(len(v) for v in self.semantic._facts.values())},
            "episodic": {"episodes": len(self.episodic._episodes)},
            "procedural": {"insights": len(self.procedural._insights)},
        }


enhanced_memory = EnhancedMemoryStore()
