# 开发路线图

## 第一期：核心引擎 (Phase 1) ✅ 已完成

**目标**：能跑、不崩——核心六层 + 基础可靠性

### 模块清单

| 模块 | 文件 | 状态 |
|------|------|------|
| **数据库初始化** | `src/db.py` | ✅ |
| **配置管理** | `src/config.py` | ✅ |
| **脑手分离** | `src/core/brain.py` | ✅ |
| | `src/core/hands.py` | ✅ |
| | `src/core/guardrail.py` | ✅ |
| | `src/core/agent_runtime.py` | ✅ 真正的 Agent 实例 |
| **模型接口** | `src/models/base.py` | ✅ 统一模型接口 |
| | `src/models/deepseek.py` | ✅ |
| | `src/models/openai.py` | ✅ |
| | `src/models/anthropic.py` | ✅ |
| | `src/models/circuit_breaker.py` | ✅ 三态熔断 |
| | `src/models/retry.py` | ✅ 智能重试 |
| | `src/models/router.py` | ✅ 模型路由 |
| **编排层** | `src/orchestration/coordinator.py` | ✅ |
| | `src/orchestration/planner.py` | ✅ Skill 优先匹配 |
| | `src/orchestration/dispatcher.py` | ✅ |
| | `src/orchestration/aggregator.py` | ✅ |
| | `src/orchestration/dag.py` | ✅ 拓扑排序 |
| | `src/orchestration/watchdog.py` | ✅ |
| | `src/orchestration/workflow_engine.py` | ✅ 多步骤状态机 |
| | `src/orchestration/durable.py` | ✅ @task 持久化执行 |
| | `src/orchestration/dlq_worker.py` | ✅ 死信队列 |
| **Session** | `src/session/manager.py` | ✅ |
| | `src/session/checkpoint.py` | ✅ Fork/Rollback/Replay/Snapshot |
| **记忆库** | `src/memory/store.py` | ✅ 热温冷三层 |
| | `src/memory/summarizer.py` | ✅ LLM 摘要生成 |
| | `src/memory/enhanced_memory.py` | ✅ 语义/经历/程序记忆 |
| | `src/memory/progressive.py` | ✅ 渐进式上下文 |
| | `src/memory/cold_storage.py` | ✅ S3/MinIO/本地冷存储 |
| **可观测** | `src/observability/tracing.py` | ✅ OpenTelemetry |
| | `src/observability/logger.py` | ✅ JSON 结构化日志 |
| | `src/observability/health.py` | ✅ 健康检查 + 自愈 |
| | `src/observability/cost.py` | ✅ 成本追踪 |
| **Actor 运行时** | `src/runtime/actor.py` | ✅ 含消息总线 |
| | `src/runtime/topic_bus.py` | ✅ 主题总线 |
| **工具系统** | `src/tools/registry.py` | ✅ |
| | `src/tools/sandbox.py` | ✅ |
| | `src/tools/isolated_sandbox.py` | ✅ |
| | `src/tools/idempotency.py` | ✅ |
| | `src/tools/skill.py` | ✅ Skill 系统 |
| **API** | `src/api/routes.py` | ✅ 40+ 端点 |
| | `src/api/schemas.py` | ✅ Pydantic 模型 |
| | `src/api/stream.py` | ✅ SSE 流 |
| | `src/api/auth.py` | ✅ API Key / JWT |
| | `src/api/ratelimit.py` | ✅ 滑动窗口限流 |
| | `src/api/component_registry.py` | ✅ 工具→组件绑定 |
| | `src/api/friday-sdk.js` | ✅ 前端 SDK |
| **前端** | `src/api/panel.py` | ✅ 管理面板 HTML |
| **配置** | `src/yaml_config.py` | ✅ YAML 配置 |
| **入口** | `src/main.py` | ✅ |
| **基础设施** | `docker-compose.yml` | ✅ |
| | `pyproject.toml` | ✅ pip install |
| | `config/.env.example` | ✅ |

---

## 第二期：高性能 (Phase 2) ✅ 已完成

- ✅ 模型分级调度 (`src/models/tiering.py`)
- ✅ Prompt 缓存 (`src/models/prompt_cache.py`)
- ✅ 语义缓存 (`src/models/semantic_cache.py`)
- ✅ 流式输出 (`src/models/streaming.py`)
- ✅ 死信队列 (`src/orchestration/dlq_worker.py`)

---

## 第三期：生产级 (Phase 3) 🔄 进行中

- ✅ Web 管理面板 (`/panel`)
- ✅ 健康检查 + 自愈
- ✅ 成本追踪
- ✅ 三级记忆存储
- ✅ API 认证 (API Key / JWT)
- ✅ S3 冷存储
- ⬜ Actor 分布式部署（跨进程/跨机器）
- ⬜ Grafana 仪表盘
- ⬜ 灰度发布

---

## 第四期：极致性能 (Phase 4) 🔄 进行中

- ✅ 语法约束生成 (`src/models/grammar.py`)
- ✅ 编译子图 / JIT (`src/orchestration/jit.py`)
- ✅ 工具交错执行 (`src/core/interleaved.py`)
- ✅ 渐进式上下文窗口 (`src/memory/progressive.py`)
- ✅ 投机执行 (`src/core/interleaved.py`)
- ⬜ 集成到主执行路径 (Phase 4 工具尚未默认开启)

---

## 下次更新计划

1. Phase 3: Actor 分布式 (Redis Pub/Sub + gRPC)
2. Phase 3: Grafana 仪表盘 + Prometheus metrics
3. Phase 4: 默认开启 JIT 子图编译
4. Phase 4: 默认开启投机执行策略
