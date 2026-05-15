"""死信队列 Worker —— 自动分析失败任务，支持回放和模式检测"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class DLQWorker:
    """死信队列处理器"""

    def __init__(self, db: AsyncSession, interval_s: int = 60, auto_alert_threshold: int = 10):
        self.db = db
        self.interval_s = interval_s
        self.auto_alert_threshold = auto_alert_threshold
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info(f"DLQ Worker started (interval={self.interval_s}s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("DLQ Worker stopped")

    async def quarantine(
        self,
        workflow_id: str,
        node_id: str | None,
        task_type: str,
        payload: dict,
        error: str,
        error_type: str,
        reason: str,
    ):
        """将失败任务送入死信队列"""
        await self.db.execute(
            text("""
                INSERT INTO task_dlq (workflow_id, node_id, task_type, payload, attempts, last_error, last_error_type, quarantine_reason)
                VALUES (:wf_id, :node_id, :task_type, :payload, 0, :error, :error_type, :reason)
            """),
            {
                "wf_id": workflow_id, "node_id": node_id,
                "task_type": task_type, "payload": json.dumps(payload, ensure_ascii=False),
                "error": error, "error_type": error_type, "reason": reason,
            },
        )
        await self.db.commit()
        logger.warning(f"Task quarantined: {workflow_id}/{node_id} — {reason}")

    async def replay(self, task_ids: list[str], dry_run: bool = True) -> dict:
        """回放死信任务 —— 实际重新执行任务"""
        results = {"replayed": 0, "failed": 0, "details": []}

        for task_id in task_ids:
            row = await self.db.execute(
                text("SELECT * FROM task_dlq WHERE id = :id"),
                {"id": task_id},
            )
            task = row.fetchone()
            if not task:
                results["details"].append({"task_id": task_id, "status": "not_found"})
                continue

            task_dict = dict(task._mapping)
            if dry_run:
                results["details"].append({
                    "task_id": task_id,
                    "status": "dry_run",
                    "task_type": task_dict["task_type"],
                    "error": task_dict["last_error"],
                })
                results["replayed"] += 1
            else:
                try:
                    replay_result = await self._execute_task(task_dict)

                    await self.db.execute(
                        text("""
                            UPDATE task_dlq
                            SET replayed_at = NOW(),
                                replay_success = :success,
                                payload = payload || :result_payload
                            WHERE id = :id
                        """),
                        {
                            "id": task_id,
                            "success": replay_result["success"],
                            "result_payload": json.dumps(replay_result, ensure_ascii=False, default=str),
                        },
                    )
                    await self.db.commit()

                    if replay_result["success"]:
                        results["replayed"] += 1
                        results["details"].append({
                            "task_id": task_id,
                            "status": "replayed",
                            "result": replay_result.get("content", "")[:200],
                        })
                    else:
                        results["failed"] += 1
                        results["details"].append({
                            "task_id": task_id,
                            "status": "replay_failed",
                            "error": replay_result.get("error", "Unknown"),
                        })

                except Exception as e:
                    results["failed"] += 1
                    results["details"].append({
                        "task_id": task_id,
                        "status": "failed",
                        "error": str(e),
                    })

        return results

    async def _execute_task(self, task: dict) -> dict:
        """实际执行死信任务"""
        try:
            payload = task.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)

            task_description = payload.get("task", task.get("task_type", ""))
            context = payload.get("context", {})

            from src.core.agent_runtime import AgentRuntime
            from src.tools.registry import tool_registry

            tools = tool_registry.get_schemas()
            tool_handlers = {}
            for name in tool_registry.list_tools():
                handler = tool_registry.get_handler(name)
                if handler:
                    tool_handlers[name] = handler

            agent = AgentRuntime(
                name=f"dlq-{task.get('id', 'unknown')[:8]}",
                system_prompt="你是任务恢复专家。请重试之前失败的任务，输出完整结果。",
                model="deepseek-chat",
                tools=tools,
                tool_handlers=tool_handlers,
                max_steps=6,
            )

            result = await agent.run(task_description, context)
            if result.success:
                logger.info(f"DLQ task {task.get('id')} replayed successfully")
                return {"success": True, "content": result.content, "tokens": result.tokens_used}
            else:
                logger.warning(f"DLQ task {task.get('id')} replay failed: {result.error}")
                return {"success": False, "error": result.error}

        except Exception as e:
            logger.error(f"DLQ task execution error: {e}")
            return {"success": False, "error": str(e)}

    async def get_stats(self) -> dict:
        """死信队列统计"""
        rows = await self.db.execute(
            text("""
                SELECT quarantine_reason, COUNT(*) as count
                FROM task_dlq
                WHERE archived = FALSE
                GROUP BY quarantine_reason
                ORDER BY count DESC
            """),
        )
        by_reason = {r["quarantine_reason"]: r["count"] for r in rows.fetchall()}

        total = await self.db.execute(text("SELECT COUNT(*) FROM task_dlq WHERE archived = FALSE"))
        unanalyzed = await self.db.execute(text("SELECT COUNT(*) FROM task_dlq WHERE analyzed = FALSE AND archived = FALSE"))

        return {
            "total": total.scalar(),
            "unanalyzed": unanalyzed.scalar(),
            "by_reason": by_reason,
        }

    async def _process_loop(self):
        """后台处理循环"""
        while self._running:
            try:
                await self._analyze_pending()
                await self._detect_patterns()
            except Exception as e:
                logger.error(f"DLQ Worker error: {e}")
            await asyncio.sleep(self.interval_s)

    async def _analyze_pending(self):
        """分析未处理的死信"""
        rows = await self.db.execute(
            text("""
                SELECT id, task_type, last_error_type, last_error, quarantine_reason
                FROM task_dlq
                WHERE analyzed = FALSE AND archived = FALSE
                LIMIT 50
            """),
        )
        for row in rows.fetchall():
            r = dict(row._mapping)
            analysis = await self._analyze_task(r)
            await self.db.execute(
                text("""
                    UPDATE task_dlq SET analyzed = TRUE, analysis_result = :analysis WHERE id = :id
                """),
                {"id": r["id"], "analysis": json.dumps(analysis, ensure_ascii=False)},
            )
        await self.db.commit()

    async def _analyze_task(self, task: dict) -> dict:
        """分析单个死信任务"""
        error = task.get("last_error", "")
        error_type = task.get("last_error_type", "")

        fixable = False
        suggestion = "需要人工审查"

        if error_type in ("rate_limit", "429"):
            fixable = True
            suggestion = "等待限流窗口过后自动重试"
        elif error_type in ("timeout",):
            fixable = True
            suggestion = "增加超时时间或降低任务复杂度后重试"
        elif error_type in ("circuit_breaker_open",):
            fixable = True
            suggestion = "等待熔断器恢复后自动重试"

        return {
            "fixable": fixable,
            "suggested_action": suggestion,
            "error_category": error_type,
        }

    async def _detect_patterns(self):
        """检测死信模式 —— 同类型失败 > 阈值时告警"""
        rows = await self.db.execute(
            text("""
                SELECT quarantine_reason, COUNT(*) as count
                FROM task_dlq
                WHERE quarantined_at > NOW() - INTERVAL '5 minutes'
                GROUP BY quarantine_reason
                HAVING COUNT(*) >= :threshold
            """),
            {"threshold": self.auto_alert_threshold},
        )
        for row in rows.fetchall():
            r = dict(row._mapping)
            logger.error(
                f"ALERT: {r['count']} tasks failed with reason '{r['quarantine_reason']}' in 5 minutes"
            )
