"""看门狗 (Watchdog) —— 监控 Coordinator 心跳，检测并接管僵死工作流"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class Watchdog:
    """工作流看门狗 —— 接管心跳超时的 Coordinator 并恢复执行"""

    def __init__(self, db: AsyncSession, interval_s: int = 30, heartbeat_timeout_s: int = 120):
        self.db = db
        self.interval_s = interval_s
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info(f"Watchdog started (interval={self.interval_s}s, timeout={self.heartbeat_timeout_s}s)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Watchdog stopped")

    async def _watch_loop(self):
        while self._running:
            try:
                await self._check_heartbeats()
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
            await asyncio.sleep(self.interval_s)

    async def _check_heartbeats(self):
        """检查心跳，接管超时工作流并恢复执行"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.heartbeat_timeout_s)

        rows = await self.db.execute(
            text("""
                SELECT id, status, coordinator_id, version, plan, task, user_id
                FROM agent_workflows
                WHERE status IN ('dispatching', 'executing', 'aggregating')
                  AND heartbeat_at < :cutoff
                ORDER BY heartbeat_at ASC
                LIMIT 5
            """),
            {"cutoff": cutoff},
        )
        stalled = [dict(r._mapping) for r in rows.fetchall()]

        for workflow in stalled:
            wf_id = workflow["id"]
            logger.warning(f"Watchdog: stalled workflow {wf_id} (status={workflow['status']}, heartbeat timeout)")

            claimed = await self._claim_workflow(wf_id, workflow["version"])
            if not claimed:
                continue

            await self._recover_workflow(workflow)

    async def _claim_workflow(self, wf_id: str, expected_version: int) -> bool:
        """乐观锁方式认领僵死工作流"""
        result = await self.db.execute(
            text("""
                UPDATE agent_workflows
                SET coordinator_id = 'watchdog', version = version + 1, heartbeat_at = NOW()
                WHERE id = :id AND version = :version
                RETURNING id
            """),
            {"id": wf_id, "version": expected_version},
        )
        await self.db.commit()
        claimed = result.fetchone() is not None
        if claimed:
            logger.info(f"Watchdog claimed workflow {wf_id}")
        return claimed

    async def _recover_workflow(self, workflow: dict):
        """恢复执行僵死工作流 —— 重建 DAG 并派发剩余节点"""
        wf_id = workflow["id"]
        task = workflow.get("task", "")

        try:
            plan_data = workflow.get("plan")
            if isinstance(plan_data, str):
                plan_data = json.loads(plan_data)
            if not plan_data or "nodes" not in plan_data:
                logger.error(f"Watchdog: no plan data for workflow {wf_id}, marking as failed")
                await self._mark_failed(wf_id, "No plan data for recovery")
                return

            from src.orchestration.dag import DAG, DAGNode, NodeStatus

            dag = DAG.from_plan(plan_data)

            node_rows = await self.db.execute(
                text("""
                    SELECT node_id, status, result, attempts, max_attempts
                    FROM workflow_nodes
                    WHERE workflow_id = :wf_id
                """),
                {"wf_id": wf_id},
            )
            node_records = {r["node_id"]: dict(r._mapping) for r in node_rows.fetchall()}

            for node_id, node in dag.nodes.items():
                record = node_records.get(node_id)
                if record:
                    if record["status"] == "completed":
                        node.status = NodeStatus.COMPLETED
                        node.result = record.get("result")
                    elif record["status"] == "failed":
                        node.status = NodeStatus.FAILED
                        node.attempts = record.get("attempts", 0)
                    elif record["status"] == "running":
                        node.status = NodeStatus.PENDING
                        node.attempts = record.get("attempts", 0)
                    else:
                        node.status = NodeStatus.PENDING
                else:
                    node.status = NodeStatus.PENDING

            await self._update_status(wf_id, "executing")

            from src.orchestration.dispatcher import Dispatcher
            from src.orchestration.aggregator import Aggregator

            dispatcher = Dispatcher(max_parallel=20)

            def _make_executor(_wf_id: str, _dag: DAG):
                from src.core.agent_runtime import AgentRuntime
                from src.tools.registry import tool_registry

                async def execute_node(node: DAGNode) -> dict:
                    import uuid
                    from sqlalchemy import text as sql_text

                    tools = tool_registry.get_schemas()
                    tool_handlers = {}
                    for name in tool_registry.list_tools():
                        handler = tool_registry.get_handler(name)
                        if handler:
                            tool_handlers[name] = handler

                    agent = AgentRuntime(
                        name=node.node_id,
                        system_prompt="你是任务执行专家。请完成以下子任务，输出简洁完整的结果。",
                        model="deepseek-chat",
                        tools=tools,
                        tool_handlers=tool_handlers,
                    )
                    result = await agent.run(node.task)
                    node_content = result.content if result.success else f"Error: {result.error}"
                    return {"content": node_content, "agent": agent.id, "steps": len(result.steps)}
                return execute_node

            results = await dispatcher.dispatch(dag, execute_fn=_make_executor(wf_id, dag))

            aggregator = Aggregator()
            failed = [n_id for n_id, n in dag.nodes.items() if n.status == NodeStatus.FAILED]
            degraded = self._calc_degradation(dag)

            final = await aggregator.aggregate(
                task=task,
                results=results,
                failed_nodes=failed,
                degradation_level=degraded,
            )

            await self.db.execute(
                text("""
                    UPDATE agent_workflows
                    SET status = 'completed', result = :result, degradation_level = :level,
                        coordinator_id = 'watchdog', completed_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": wf_id,
                    "result": json.dumps(final, ensure_ascii=False, default=str),
                    "level": degraded,
                },
            )
            await self.db.commit()
            logger.info(f"Watchdog recovered workflow {wf_id}: {len(results)} nodes completed")

        except Exception as e:
            logger.error(f"Watchdog recovery failed for {wf_id}: {e}")
            await self._mark_failed(wf_id, f"Watchdog recovery error: {e}")

    async def _mark_failed(self, wf_id: str, error: str):
        try:
            await self.db.execute(
                text("""
                    UPDATE agent_workflows
                    SET status = 'failed', error = :error, completed_at = NOW()
                    WHERE id = :id
                """),
                {"id": wf_id, "error": error[:500]},
            )
            await self.db.commit()
        except Exception:
            pass

    async def _update_status(self, wf_id: str, status: str):
        await self.db.execute(
            text("UPDATE agent_workflows SET status = :status, heartbeat_at = NOW() WHERE id = :id"),
            {"id": wf_id, "status": status},
        )
        await self.db.commit()

    @staticmethod
    def _calc_degradation(dag) -> int:
        from src.orchestration.dag import NodeStatus
        has_failed = any(n.status == NodeStatus.FAILED for n in dag.nodes.values())
        if has_failed:
            has_critical_fail = any(
                n.status == NodeStatus.FAILED and n.is_critical
                for n in dag.nodes.values()
            )
            if has_critical_fail:
                return 3
            return 2
        all_completed = all(
            n.status == NodeStatus.COMPLETED for n in dag.nodes.values()
        )
        return 0 if all_completed else 3
