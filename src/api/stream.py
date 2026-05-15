"""周五流协议 —— SSE 标准事件，任何前端都能接

基于 Vercel AI SDK 的 data: 协议扩展

标准事件:
  start, text-start/delta/end, tool-input-start/delta/available,
  tool-output-available/error, finish-step, finish

星期五扩展事件:
  workflow-step (开始/完成某步), workflow-approval-requested,
  workflow-navigate (用户回退), batch-progress,
  file-ready, session-join/leave
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class StreamEvent:
    """流事件"""
    def __init__(self, event_type: str, **data):
        self.type = event_type
        self.id = str(uuid.uuid4())[:8]
        self.data = data
        self.timestamp = time.time()

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, **self.data}, ensure_ascii=False)
        return f"id: {self.id}\ndata: {payload}\n\n"

    @classmethod
    def from_payload(cls, payload: dict) -> "StreamEvent":
        event = cls(str(payload.get("type") or "message"), **dict(payload.get("data") or {}))
        event.id = str(payload.get("id") or event.id)
        event.timestamp = float(payload.get("timestamp") or event.timestamp)
        return event


class FridayStream:
    """周五流 —— SSE 事件流管理器"""

    def __init__(self):
        self._listeners: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._message_id = 0
        self._instance_id = uuid.uuid4().hex
        self._remote_tasks: dict[int, asyncio.Task] = {}

    def subscribe(self, channel_id: str = "global", include_remote: bool = False) -> asyncio.Queue:
        """订阅事件流，返回一个异步队列"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners[channel_id].append(queue)
        if include_remote:
            task = asyncio.create_task(self._pump_remote_events(channel_id, queue))
            self._remote_tasks[id(queue)] = task
        return queue

    def unsubscribe(self, channel_id: str, queue: asyncio.Queue):
        listeners = self._listeners.get(channel_id, [])
        if queue in listeners:
            listeners.remove(queue)
        if not listeners and channel_id in self._listeners:
            self._listeners.pop(channel_id, None)
        task = self._remote_tasks.pop(id(queue), None)
        if task is not None:
            task.cancel()

    async def emit(self, event: StreamEvent, channel_id: str = "global"):
        """发送事件给指定通道的订阅者"""
        for queue in list(self._listeners.get(channel_id, [])):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Stream listener queue full, dropping event")
        await self._publish_remote_event(event, channel_id)

    # ── 标准事件 ──

    async def start(self, message_id: str = "", workflow_id: str = "global"):
        await self.emit(StreamEvent("start", messageId=message_id, workflowId=workflow_id), workflow_id)

    async def text_start(self, text_id: str = "", workflow_id: str = "global"):
        await self.emit(StreamEvent("text-start", id=text_id, workflowId=workflow_id), workflow_id)

    async def text_delta(self, text_id: str, delta: str, workflow_id: str = "global"):
        await self.emit(StreamEvent("text-delta", id=text_id, delta=delta, workflowId=workflow_id), workflow_id)

    async def text_end(self, text_id: str, workflow_id: str = "global"):
        await self.emit(StreamEvent("text-end", id=text_id, workflowId=workflow_id), workflow_id)

    async def tool_input_start(self, tool_call_id: str, tool_name: str, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("tool-input-start", toolCallId=tool_call_id, toolName=tool_name, workflowId=workflow_id),
            workflow_id,
        )

    async def tool_input_delta(self, tool_call_id: str, delta: str, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("tool-input-delta", toolCallId=tool_call_id, inputTextDelta=delta, workflowId=workflow_id),
            workflow_id,
        )

    async def tool_input_available(self, tool_call_id: str, tool_name: str, input_data: dict, workflow_id: str = "global"):
        await self.emit(
            StreamEvent(
                "tool-input-available",
                toolCallId=tool_call_id,
                toolName=tool_name,
                input=input_data,
                workflowId=workflow_id,
            ),
            workflow_id,
        )

    async def tool_output_available(self, tool_call_id: str, output: dict, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("tool-output-available", toolCallId=tool_call_id, output=output, workflowId=workflow_id),
            workflow_id,
        )

    async def tool_output_error(self, tool_call_id: str, error: str, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("tool-output-error", toolCallId=tool_call_id, errorText=error, workflowId=workflow_id),
            workflow_id,
        )

    async def finish_step(self, step_id: str = "", workflow_id: str = "global"):
        await self.emit(StreamEvent("finish-step", stepId=step_id, workflowId=workflow_id), workflow_id)

    async def finish(self, usage: dict | None = None, workflow_id: str = "global"):
        await self.emit(StreamEvent("finish", usage=usage or {}, workflowId=workflow_id), workflow_id)

    # ── 星期五扩展事件 ──

    async def workflow_step_start(
        self,
        step_id: str,
        step_name: str,
        step_index: int,
        total_steps: int,
        workflow_id: str = "global",
    ):
        await self.emit(
            StreamEvent(
                "workflow-step",
                stepId=step_id,
                stepName=step_name,
                stepIndex=step_index,
                totalSteps=total_steps,
                status="started",
                workflowId=workflow_id,
            ),
            workflow_id,
        )

    async def workflow_step_complete(self, step_id: str, output: dict = None, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("workflow-step", stepId=step_id, status="completed", output=output or {}, workflowId=workflow_id),
            workflow_id,
        )

    async def workflow_step_error(self, step_id: str, error: str, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("workflow-step", stepId=step_id, status="error", error=error, workflowId=workflow_id),
            workflow_id,
        )

    async def workflow_approval_requested(self, step_id: str, message: str, options: list = None, workflow_id: str = "global"):
        await self.emit(
            StreamEvent(
                "workflow-approval-requested",
                stepId=step_id,
                message=message,
                options=options or [],
                workflowId=workflow_id,
            ),
            workflow_id,
        )

    async def workflow_navigate(self, step_from: str, step_to: str, workflow_id: str = "global"):
        await self.emit(
            StreamEvent("workflow-navigate", fromStep=step_from, toStep=step_to, workflowId=workflow_id),
            workflow_id,
        )

    async def batch_progress(self, batch_id: str, completed: int, total: int, message: str = "", workflow_id: str = "global"):
        await self.emit(
            StreamEvent(
                "batch-progress",
                batchId=batch_id,
                completed=completed,
                total=total,
                message=message,
                workflowId=workflow_id,
            ),
            workflow_id,
        )

    async def file_ready(self, file_id: str, url: str, filename: str, size_bytes: int = 0, workflow_id: str = "global"):
        await self.emit(
            StreamEvent(
                "file-ready",
                fileId=file_id,
                url=url,
                filename=filename,
                sizeBytes=size_bytes,
                workflowId=workflow_id,
            ),
            workflow_id,
        )

    async def session_join(self, user_id: str, session_id: str, workflow_id: str = "global"):
        await self.emit(StreamEvent("session-join", userId=user_id, sessionId=session_id, workflowId=workflow_id), workflow_id)

    async def session_leave(self, user_id: str, workflow_id: str = "global"):
        await self.emit(StreamEvent("session-leave", userId=user_id, workflowId=workflow_id), workflow_id)

    async def _publish_remote_event(self, event: StreamEvent, channel_id: str) -> None:
        try:
            from src.db import get_redis

            redis = await get_redis()
            await redis.publish(
                self._redis_channel(channel_id),
                json.dumps(
                    {
                        "source": self._instance_id,
                        "event": {
                            "type": event.type,
                            "id": event.id,
                            "timestamp": event.timestamp,
                            "data": event.data,
                        },
                    },
                    ensure_ascii=False,
                ),
            )
        except Exception:
            # Redis 不可用时退回进程内广播
            return

    async def _pump_remote_events(self, channel_id: str, queue: asyncio.Queue) -> None:
        pubsub = None
        try:
            from src.db import get_redis

            redis = await get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe(self._redis_channel(channel_id))

            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    await asyncio.sleep(0.05)
                    continue
                raw = message.get("data")
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                if not isinstance(raw, str) or not raw:
                    continue
                try:
                    envelope = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if envelope.get("source") == self._instance_id:
                    continue
                event_payload = envelope.get("event")
                if not isinstance(event_payload, dict):
                    continue
                try:
                    queue.put_nowait(StreamEvent.from_payload(event_payload))
                except asyncio.QueueFull:
                    logger.warning("Remote stream listener queue full, dropping event")
        except asyncio.CancelledError:
            raise
        except Exception:
            return
        finally:
            if pubsub is not None:
                try:
                    await pubsub.unsubscribe(self._redis_channel(channel_id))
                    await pubsub.close()
                except Exception:
                    pass

    @staticmethod
    def _redis_channel(channel_id: str) -> str:
        return f"friday:stream:{channel_id}"


# 全局流实例
friday_stream = FridayStream()


async def sse_generator(stream: FridayStream, channel_id: str, timeout: float = 30) -> AsyncIterator[str]:
    """将 FridayStream 转换为 SSE 生成器 (FastAPI StreamingResponse)"""
    queue = stream.subscribe(channel_id, include_remote=True)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
                yield event.to_sse()
            except asyncio.TimeoutError:
                # 发送心跳
                yield f": heartbeat {int(time.time())}\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        stream.unsubscribe(channel_id, queue)
