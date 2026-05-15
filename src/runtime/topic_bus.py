"""主题发布订阅增强 —— AutoGen 风格的按主题路由消息"""

import asyncio
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class TopicMessage:
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    topic: str = ""
    sender: str = ""
    msg_type: str = ""    # event | request | response | command
    payload: Any = None
    correlation_id: str | None = None
    timestamp: float = field(default_factory=lambda: __import__("time").time())


class TopicBus:
    """主题总线 —— 按主题路由消息，发布者与订阅者完全解耦"""

    def __init__(self):
        self._subscriptions: dict[str, dict[str, Callable]] = defaultdict(dict)
        # topic → { subscriber_id: handler_fn }
        self._pattern_subscriptions: list[tuple[str, str, Callable]] = []
        # (pattern, subscriber_id, handler_fn)
        self._message_log: list[TopicMessage] = []
        self._max_log = 1000

    def subscribe(self, topic: str, subscriber_id: str, handler: Callable[..., Awaitable]):
        """订阅主题"""
        self._subscriptions[topic][subscriber_id] = handler
        logger.debug(f"Topic subscription: {subscriber_id} → {topic}")

    def subscribe_pattern(self, pattern: str, subscriber_id: str, handler: Callable[..., Awaitable]):
        """订阅匹配模式的主题（如 agent.*.task）"""
        self._pattern_subscriptions.append((pattern, subscriber_id, handler))

    def unsubscribe(self, topic: str, subscriber_id: str):
        if topic in self._subscriptions:
            self._subscriptions[topic].pop(subscriber_id, None)

    def unsubscribe_all(self, subscriber_id: str):
        for topic in self._subscriptions:
            self._subscriptions[topic].pop(subscriber_id, None)
        self._pattern_subscriptions = [
            (p, s, h) for p, s, h in self._pattern_subscriptions if s != subscriber_id
        ]

    async def publish(self, message: TopicMessage):
        """发布消息到主题"""
        self._message_log.append(message)
        if len(self._message_log) > self._max_log:
            self._message_log = self._message_log[-self._max_log:]

        tasks = []

        # 精确匹配
        if message.topic in self._subscriptions:
            for sub_id, handler in self._subscriptions[message.topic].items():
                tasks.append(self._safe_call(handler, message, sub_id))

        # 模式匹配
        import re
        for pattern, sub_id, handler in self._pattern_subscriptions:
            if self._match_pattern(pattern, message.topic):
                tasks.append(self._safe_call(handler, message, sub_id))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def request(self, topic: str, payload: Any, sender: str = "", timeout: float = 30) -> Any:
        """请求-响应模式: 发布请求，等待响应"""
        msg = TopicMessage(
            topic=topic, sender=sender, msg_type="request",
            payload=payload, correlation_id=uuid.uuid4().hex,
        )
        # 简化: 调用订阅者并返回第一个结果
        if topic in self._subscriptions:
            for handler in self._subscriptions[topic].values():
                try:
                    return await asyncio.wait_for(handler(msg), timeout=timeout)
                except asyncio.TimeoutError:
                    raise
                except Exception as e:
                    logger.error(f"Topic request failed: {e}")
        raise ValueError(f"No subscriber for topic: {topic}")

    def _match_pattern(self, pattern: str, topic: str) -> bool:
        """简单通配符匹配: * 匹配任意字符"""
        regex = pattern.replace(".", "\\.").replace("*", ".*")
        regex = f"^{regex}$"
        return bool(re.match(regex, topic))

    async def _safe_call(self, handler: Callable, message: TopicMessage, sub_id: str):
        try:
            await handler(message)
        except Exception as e:
            logger.error(f"Topic handler error [{sub_id}]: {e}")

    def get_stats(self) -> dict:
        return {
            "topics": list(self._subscriptions.keys()),
            "subscribers": sum(len(subs) for subs in self._subscriptions.values()),
            "pattern_subscriptions": len(self._pattern_subscriptions),
            "messages_logged": len(self._message_log),
        }


# 全局主题总线
topic_bus = TopicBus()


# ── 预定义系统主题 ──
SYSTEM_TOPICS = {
    "agent.task.started": "Agent 开始执行任务",
    "agent.task.completed": "Agent 任务完成",
    "agent.task.failed": "Agent 任务失败",
    "workflow.step.started": "工作流步骤开始",
    "workflow.step.completed": "工作流步骤完成",
    "workflow.approval.requested": "请求审批",
    "file.generated": "文件生成完毕",
    "cost.threshold.warning": "成本接近阈值",
    "rate_limit.warning": "速率限制告警",
    "system.health.change": "系统健康状态变化",
}


async def setup_system_subscribers():
    """注册系统级订阅者"""
    
    async def log_all_events(msg: TopicMessage):
        logger.info(f"[Topic:{msg.topic}] {msg.sender}: {str(msg.payload)[:200]}")

    async def cost_alert(msg: TopicMessage):
        from src.observability.cost import cost_tracker
        logger.warning(f"Cost alert: {msg.payload} | Total: ${cost_tracker.total_cost():.4f}")

    async def rate_limit_alert(msg: TopicMessage):
        logger.warning(f"Rate limit: {msg.payload}")

    topic_bus.subscribe("*", "system-logger", log_all_events)
    topic_bus.subscribe("cost.threshold.warning", "cost-monitor", cost_alert)
    topic_bus.subscribe("rate_limit.warning", "rate-monitor", rate_limit_alert)


import re
