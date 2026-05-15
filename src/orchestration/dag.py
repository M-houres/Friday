"""DAG 状态机 —— 任务依赖图 + 拓扑排序"""

from dataclasses import dataclass, field
from enum import Enum
from collections import deque


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    node_id: str
    task: str
    dependencies: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: dict | None = None
    agent_id: str | None = None
    skill_name: str | None = None      # 匹配到的 Skill 名
    tools: list[str] | None = None     # 可用的工具列表
    model: str | None = None
    error: str = ""
    priority: int = 5
    attempts: int = 0
    max_attempts: int = 3
    is_critical: bool = True  # False = 可以降级跳过


@dataclass
class DAG:
    """有向无环图 —— 任务编排的核心数据结构"""
    nodes: dict[str, DAGNode] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (from, to)

    def add_node(self, node: DAGNode):
        self.nodes[node.node_id] = node

    def add_edge(self, from_id: str, to_id: str):
        if from_id in self.nodes and to_id in self.nodes:
            self.edges.append((from_id, to_id))
            if from_id not in self.nodes[to_id].dependencies:
                self.nodes[to_id].dependencies.append(from_id)

    def get_ready_nodes(self) -> list[DAGNode]:
        """返回所有就绪节点（依赖全部完成，自己未开始）"""
        ready = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            if all(
                self.nodes[dep].status == NodeStatus.COMPLETED
                for dep in node.dependencies
            ):
                ready.append(node)
        return ready

    def is_complete(self) -> bool:
        return all(
            node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED, NodeStatus.FAILED)
            for node in self.nodes.values()
        )

    def is_success(self) -> bool:
        return all(
            node.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED)
            for node in self.nodes.values()
        )

    def topological_order(self) -> list[str]:
        """拓扑排序 —— 返回节点 ID 列表"""
        in_degree = {n_id: len(node.dependencies) for n_id, node in self.nodes.items()}
        queue = deque([n_id for n_id, d in in_degree.items() if d == 0])
        order = []

        while queue:
            current = queue.popleft()
            order.append(current)
            for from_id, to_id in self.edges:
                if from_id == current:
                    in_degree[to_id] -= 1
                    if in_degree[to_id] == 0:
                        queue.append(to_id)

        return order

    def get_critical_path(self) -> list[str]:
        """识别关键路径 —— 用于优先级提升"""
        order = self.topological_order()
        if not order:
            return []

        # 简化版：假设每个节点耗时相同，最长路径是关键路径
        dist = {n_id: 0 for n_id in self.nodes}
        for n_id in order:
            for from_id, to_id in self.edges:
                if from_id == n_id:
                    dist[to_id] = max(dist[to_id], dist[n_id] + 1)

        # 从最远节点回溯
        end_node = max(dist, key=dist.get)
        path = [end_node]
        while True:
            current = path[-1]
            predecessors = [f for f, t in self.edges if t == current]
            if not predecessors:
                break
            furthest_pred = max(predecessors, key=lambda p: dist[p])
            path.append(furthest_pred)

        return list(reversed(path))

    def to_dict(self) -> dict:
        return {
            "nodes": {
                n_id: {
                    "node_id": node.node_id,
                    "task": node.task,
                    "dependencies": node.dependencies,
                    "status": node.status.value,
                    "is_critical": node.is_critical,
                }
                for n_id, node in self.nodes.items()
            },
            "edges": self.edges,
        }

    @classmethod
    def from_plan(cls, plan: dict) -> "DAG":
        """从 Planner 产出的计划构建 DAG —— 兼容 LLM 和 Skill 两种产出格式"""
        dag = cls()
        counter = 0
        for node_data in plan.get("nodes", []):
            node_id = node_data.get("node_id") or node_data.get("id") or f"step_{counter}"
            task = node_data.get("task") or node_data.get("name") or node_data.get("title") or f"任务 {counter}"
            deps = node_data.get("dependencies") or node_data.get("depends_on") or []
            skill_name = node_data.get("skill_name")
            tools = node_data.get("tools")
            dag.add_node(DAGNode(
                node_id=node_id,
                task=task,
                dependencies=deps,
                is_critical=node_data.get("is_critical", True),
                skill_name=skill_name,
                tools=tools,
            ))
            counter += 1
        for edge in plan.get("edges", []):
            if isinstance(edge, list) and len(edge) >= 2:
                dag.add_edge(edge[0], edge[1])
        return dag
