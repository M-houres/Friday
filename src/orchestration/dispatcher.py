"""派发器 (Dispatcher) —— 按拓扑序派发任务给 Agent"""

import asyncio
import logging
from typing import Awaitable, Callable

from src.orchestration.dag import DAG, DAGNode, NodeStatus

logger = logging.getLogger(__name__)

ExecuteFn = Callable[[DAGNode], Awaitable[dict]]


class Dispatcher:
    """任务派发器 —— 管理并行执行，处理依赖关系"""

    def __init__(self, max_parallel: int = 20):
        self.max_parallel = max_parallel
        self._semaphore = asyncio.Semaphore(max_parallel)

    async def dispatch(
        self,
        dag: DAG,
        execute_fn: ExecuteFn,
        on_progress: Callable[[DAGNode], Awaitable] | None = None,
    ) -> dict[str, dict]:
        """按拓扑序派发所有就绪节点，直到全部完成"""
        results: dict[str, dict] = {}
        running_tasks: dict[str, asyncio.Task] = {}

        while not dag.is_complete():
            ready = dag.get_ready_nodes()

            if not ready and not running_tasks:
                # 可能死锁或全部失败
                logger.warning("DAG stalled — no ready nodes and no running tasks")
                break

            # 启动就绪节点
            for node in ready:
                if len(running_tasks) >= self.max_parallel:
                    break
                node.status = NodeStatus.RUNNING
                task = asyncio.create_task(self._execute_node(node, execute_fn))
                running_tasks[node.node_id] = task

            if not running_tasks:
                await asyncio.sleep(0.1)
                continue

            # 等待任一任务完成
            done, _ = await asyncio.wait(
                running_tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=10.0,
            )

            for completed_task in done:
                # 找到对应的 node_id
                for n_id, t in list(running_tasks.items()):
                    if t is completed_task:
                        del running_tasks[n_id]
                        try:
                            node = dag.nodes[n_id]
                            result = completed_task.result()
                            node.result = result
                            node.status = NodeStatus.COMPLETED
                            results[n_id] = result
                            logger.info(f"Node {n_id} completed")

                            if on_progress:
                                await on_progress(node)

                        except Exception as e:
                            node = dag.nodes[n_id]
                            node.attempts += 1
                            logger.error(f"Node {n_id} failed (attempt {node.attempts}): {e}")

                            if node.attempts < node.max_attempts:
                                node.status = NodeStatus.PENDING
                                logger.info(f"Node {n_id} will retry")
                            else:
                                node.status = NodeStatus.FAILED
                                node.error = str(e)
                                results[n_id] = {"error": str(e)}
                                logger.error(f"Node {n_id} permanently failed")
                        break

        return results

    async def _execute_node(self, node: DAGNode, execute_fn: ExecuteFn) -> dict:
        async with self._semaphore:
            return await execute_fn(node)
