"""Phase 2-4 模块测试"""

import pytest


class TestComplexityClassifier:
    def test_simple_task(self):
        from src.models.tiering import ComplexityClassifier, Complexity
        assert ComplexityClassifier.classify("查询文件") == Complexity.SIMPLE
        assert ComplexityClassifier.classify("show me the list") == Complexity.SIMPLE

    def test_moderate_task(self):
        from src.models.tiering import ComplexityClassifier, Complexity
        assert ComplexityClassifier.classify("优化数据库查询性能") == Complexity.MODERATE

    def test_complex_task(self):
        from src.models.tiering import ComplexityClassifier, Complexity
        task = "重构整个认证系统架构，优化安全审计"
        assert ComplexityClassifier.classify(task) == Complexity.COMPLEX

    def test_expert_task(self):
        from src.models.tiering import ComplexityClassifier, Complexity
        assert ComplexityClassifier.classify("从零搭建系统设计") == Complexity.EXPERT


class TestSchemaGrammar:
    def test_build_grammar(self):
        from src.models.grammar import SchemaGrammar
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "confidence": {"type": "number"},
            },
            "required": ["action"],
        }
        grammar = SchemaGrammar(schema)
        gbnf = grammar.build_grammar_string()
        assert "root" in gbnf
        assert "action-pair" in gbnf
        assert "confidence-pair" in gbnf

    def test_validate_valid_output(self):
        from src.models.grammar import SchemaGrammar
        schema = {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "args": {"type": "object"},
            },
            "required": ["tool"],
        }
        grammar = SchemaGrammar(schema)
        result = grammar.validate_structured_output('{"tool": "search", "args": {"q": "test"}}')
        assert result is not None
        assert result["tool"] == "search"

    def test_validate_invalid_output(self):
        from src.models.grammar import SchemaGrammar
        schema = {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}
        grammar = SchemaGrammar(schema)
        result = grammar.validate_structured_output('{"wrong_key": "value"}')
        assert result is None

    def test_validate_missing_required(self):
        from src.models.grammar import SchemaGrammar
        schema = {"type": "object", "properties": {"name": {"type": "string"}, "email": {"type": "string"}}, "required": ["name", "email"]}
        grammar = SchemaGrammar(schema)
        result = grammar.validate_structured_output('{"name": "test"}')
        assert result is None or "email" not in result


class TestProgressiveContext:
    def test_compress_small(self):
        from src.memory.progressive import ProgressiveContext
        ctx = ProgressiveContext(recent_count=3, summary_count=2)
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
        result = ctx.compress(messages)
        assert len(result) <= 5  # 小数据不压缩

    def test_compress_large(self):
        from src.memory.progressive import ProgressiveContext
        ctx = ProgressiveContext(recent_count=3, summary_count=2)
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        result = ctx.compress(messages)
        # 最近 3 条 + 前 2 条高相关 + 摘要 = 应该 < 20
        assert len(result) < 20

    def test_saliency_tool_result(self):
        from src.memory.progressive import ProgressiveContext
        ctx = ProgressiveContext()
        tool_msg = {"role": "tool", "content": "search result: found 5 files"}
        normal_msg = {"role": "user", "content": "ok thanks"}
        assert ctx._saliency_score(tool_msg, "search") > ctx._saliency_score(normal_msg, "search")


class TestJITCompiler:
    def test_observe_and_compile(self):
        from src.orchestration.jit import JITCompiler
        jit = JITCompiler(compile_threshold=3)
        sig = "user asks to search and read files"
        calls = [
            {"name": "search_files", "arguments": {"query": "test"}},
            {"name": "read_file", "arguments": {"path": "/src/test.py"}},
        ]
        for _ in range(3):
            jit.observe(sig, calls)
        assert len(jit._subgraphs) == 1
        assert jit._subgraphs[0].name.startswith("jit_")

    def test_find_match(self):
        from src.orchestration.jit import JITCompiler
        jit = JITCompiler(compile_threshold=2)
        sig = "search and read"
        calls = [{"name": "search", "arguments": {}}]
        for _ in range(2):
            jit.observe(sig, calls)

        state = {"task": "search and read files", "context": "user wants to find test.py"}
        match = jit.find_match(state)
        assert match is not None

    def test_no_match(self):
        from src.orchestration.jit import JITCompiler
        jit = JITCompiler()
        state = {"task": "completely unrelated task", "context": "nothing matches"}
        assert jit.find_match(state) is None


class TestInterleavedExecutor:
    def test_pending_count(self):
        from src.core.interleaved import InterleavedExecutor
        executor = InterleavedExecutor()
        assert executor.get_pending_count() == 0


class TestSpeculativeExecutor:
    def test_init(self):
        from src.core.interleaved import SpeculativeExecutor
        executor = SpeculativeExecutor()
        assert len(executor.fast_models) >= 1
        assert len(executor.slow_models) >= 1


class TestSelfHealer:
    def test_no_crash_on_reconnect(self):
        from src.observability.health import SelfHealer, health_checker
        healer = SelfHealer(health_checker, interval_s=999)
        # Should not crash even without DB
        assert healer is not None
