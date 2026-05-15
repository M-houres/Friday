"""编译子图 —— 检测重复模式，跳过 LLM，直接执行预编译路径"""

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


class CompiledSubgraph:
    """预编译的子图 —— 匹配条件 + 固定工具调用序列"""
    
    def __init__(self, name: str, conditions: list[Callable], actions: list[dict], priority: int = 5):
        self.name = name
        self.conditions = conditions  # list of callable(state) -> bool
        self.actions = actions  # list of {tool_name, args_template}
        self.priority = priority
        self.hit_count: int = 0
        self.success_count: int = 0

    def matches(self, state: dict) -> bool:
        return all(cond(state) for cond in self.conditions)

    async def execute(self, tool_executor: Callable[..., Awaitable]) -> list:
        results = []
        for action in self.actions:
            result = await tool_executor(action["tool_name"], action.get("args_template", {}))
            results.append(result)
        return results

    @property
    def hit_rate(self) -> float:
        if self.hit_count == 0:
            return 0.0
        return self.success_count / self.hit_count


class JITCompiler:
    """JIT 编译器 —— 运行时检测模式，自动编译为子图"""
    
    def __init__(self, compile_threshold: int = 5):
        self.compile_threshold = compile_threshold
        self._subgraphs: list[CompiledSubgraph] = []
        self._pattern_freq: dict[str, int] = defaultdict(int)
        self._pattern_trace: dict[str, list[dict]] = defaultdict(list)

    def observe(self, state_signature: str, tool_calls: list[dict]):
        """观察一个执行模式"""
        key = hashlib.sha256(state_signature.encode()).hexdigest()[:16]
        self._pattern_freq[key] += 1
        self._pattern_trace[key] = tool_calls

        # 达到阈值 → 编译
        if self._pattern_freq[key] >= self.compile_threshold and not self._is_compiled(key):
            self._compile(key, state_signature, tool_calls)

    def _is_compiled(self, pattern_key: str) -> bool:
        for sg in self._subgraphs:
            if sg.name.endswith(pattern_key):
                return True
        return False

    def _compile(self, pattern_key: str, signature: str, tool_calls: list[dict]):
        """编译模式为子图"""
        conditions = self._extract_conditions(signature)
        actions = [
            {"tool_name": tc.get("name", tc.get("tool", "")), "args_template": tc.get("arguments", {})}
            for tc in tool_calls
        ]

        subgraph = CompiledSubgraph(
            name=f"jit_{pattern_key}",
            conditions=conditions,
            actions=actions,
        )
        self._subgraphs.append(subgraph)
        logger.info(f"JIT compiled subgraph '{subgraph.name}' with {len(actions)} actions")

    def _extract_conditions(self, signature: str) -> list[Callable]:
        """从特征签名提取匹配条件"""
        # 简化：关键词匹配
        keywords = signature.lower().split()

        def make_check(kw):
            def check(state: dict) -> bool:
                state_str = json.dumps(state, ensure_ascii=False).lower()
                return kw in state_str
            return check

        return [make_check(kw) for kw in keywords[:5]]

    def find_match(self, state: dict) -> CompiledSubgraph | None:
        """查找匹配的子图"""
        matches = [sg for sg in self._subgraphs if sg.matches(state)]
        if matches:
            # 返回优先级最高（数字最小）的
            best = min(matches, key=lambda sg: (sg.priority, -sg.hit_rate))
            best.hit_count += 1
            return best
        return None

    def record_success(self, subgraph: CompiledSubgraph):
        subgraph.success_count += 1

    def get_stats(self) -> dict:
        return {
            "compiled_subgraphs": len(self._subgraphs),
            "total_hits": sum(sg.hit_count for sg in self._subgraphs),
            "subgraphs": [
                {
                    "name": sg.name,
                    "actions": len(sg.actions),
                    "hit_rate": f"{sg.hit_rate:.1%}",
                    "hits": sg.hit_count,
                }
                for sg in sorted(self._subgraphs, key=lambda s: s.hit_count, reverse=True)[:10]
            ],
        }


jit_compiler = JITCompiler(compile_threshold=5)
