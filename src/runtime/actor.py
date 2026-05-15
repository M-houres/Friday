"""Actor 运行时 —— asyncio + 消息总线"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ActorMessage:
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    sender: str = ""
    recipient: str = ""
    msg_type: str = ""  # task | result | error | control
    payload: Any = None
    correlation_id: str | None = None


class MessageBus:
    """消息总线 —— Actor 间通信"""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}

    def register(self, actor_id: str, maxsize: int = 0):
        self._queues[actor_id] = asyncio.Queue(maxsize=maxsize)

    def unregister(self, actor_id: str):
        self._queues.pop(actor_id, None)

    async def send(self, message: ActorMessage):
        """发送消息到指定 Actor"""
        queue = self._queues.get(message.recipient)
        if queue is None:
            raise ValueError(f"Recipient not found: {message.recipient}")
        await queue.put(message)

    async def publish(self, topic: str, message: ActorMessage):
        """发布消息到所有注册 Actor（简化版：广播）"""
        for actor_id, queue in self._queues.items():
            if actor_id != message.sender:
                msg_copy = ActorMessage(
                    msg_id=message.msg_id,
                    sender=message.sender,
                    recipient=actor_id,
                    msg_type=message.msg_type,
                    payload=message.payload,
                    correlation_id=message.correlation_id,
                )
                await queue.put(msg_copy)

    async def receive(self, actor_id: str, timeout: float | None = None) -> ActorMessage:
        """接收消息"""
        queue = self._queues.get(actor_id)
        if queue is None:
            raise ValueError(f"Actor not registered: {actor_id}")
        if timeout:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        return await queue.get()


class Actor:
    """Actor 基类 —— 每个 Agent 是一个 Actor"""

    def __init__(self, actor_id: str, bus: MessageBus):
        self.actor_id = actor_id
        self.bus = bus
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self.bus.register(self.actor_id)
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Actor {self.actor_id} started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        self.bus.unregister(self.actor_id)
        logger.info(f"Actor {self.actor_id} stopped")

    async def _run_loop(self):
        """消息循环 —— 子类重写 on_message"""
        while self._running:
            try:
                message = await self.bus.receive(self.actor_id, timeout=1.0)
                await self.on_message(message)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Actor {self.actor_id} error: {e}")

    async def on_message(self, message: ActorMessage):
        """处理消息 —— 子类实现"""
        pass

    async def send(self, recipient: str, msg_type: str, payload: Any = None, correlation_id: str | None = None):
        await self.bus.send(ActorMessage(
            sender=self.actor_id,
            recipient=recipient,
            msg_type=msg_type,
            payload=payload,
            correlation_id=correlation_id,
        ))


# 全局消息总线
message_bus = MessageBus()
