# 架构设计

## 六层解耦

### 第一层：脑手分离 (Brain-Hand Separation)

**问题**：传统 Agent 系统将模型推理和执行环境耦合，一个环节崩溃导致整体不可用。

**设计**：

```
┌──────────────────┐     ┌──────────────────┐
│   大脑 (Brain)    │────▶│   双手 (Hands)    │
│                  │     │                  │
│ - 模型推理       │     │ - 沙盒执行       │
│ - 决策制定       │     │ - 工具调用       │
│ - 规划思考       │     │ - 结果回传       │
│                  │◀────│                  │
└──────────────────┘     └──────────────────┘
     无状态                   有状态但隔离
     可随时替换               崩溃不影响大脑
```

**关键模块**：

```python
# src/core/brain.py
class Brain:
    """模型推理抽象层"""
    provider: ModelProvider      # DeepSeek / OpenAI / Claude
    strategy: PlanningStrategy   # ReAct / PlanExecute / Reflection
    
    async def think(self, context: AgentContext) -> Thought:
        """输入上下文，输出决策"""

# src/core/hands.py
class Hand:
    """沙盒执行环境"""
    sandbox: Sandbox             # Subprocess / Docker
    tools: ToolRegistry          # 可用工具集
    guard: Guardrail             # 输入输出护栏
    
    async def execute(self, action: Action) -> ActionResult:
        """执行动作，返回结果"""
```

**沙盒切换**：

```python
# config 里一行切换
SANDBOX_TYPE = "docker"   # subprocess | docker
```

---

### 第二层：编排层 (Orchestration Layer)

**Coordinator 职责**：接收任务 → 拆解 → 派发 → 聚合 → 返回

```
                    用户任务
                       │
                       ▼
              ┌────────────────┐
              │   Planner      │  分析意图，生成 DAG
              │   (计划器)     │
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │   Dispatcher   │  按拓扑序派发任务
              │   (派发器)     │
              └───────┬────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
      Agent 1     Agent 2     Agent 3
          │           │           │
          └───────────┼───────────┘
                      ▼
              ┌────────────────┐
              │   Aggregator   │  收集结果，拼装输出
              │   (聚合器)     │
              └────────────────┘
```

**五种编排模式**：

| 模式 | 适用场景 | 实现 |
|------|---------|------|
| **串行链** | 步骤有强依赖性 | SequentialPipeline |
| **并行分块 + 投票** | 独立子任务 / 多视角评估 | ParallelVoting |
| **指挥-工人** | 动态拆解，子任务数不确定 | OrchestratorWorkers |
| **评估-优化循环** | 有明确评价标准 | EvaluatorOptimizer |
| **群聊协商** | 复杂协作推理 | GroupChat |

**DAG 状态机**：

```sql
-- 工作流状态持久化，Coordinator 崩溃可恢复
CREATE TABLE agent_workflows (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL,         -- PLANNING|DISPATCHING|EXECUTING|AGGREGATING|COMPLETED|FAILED
    plan JSONB,                   -- 生成的 DAG
    nodes_completed TEXT[],
    nodes_failed TEXT[],
    result JSONB,
    heartbeat_at TIMESTAMPTZ,     -- Coordinator 心跳
    coordinator_id TEXT,
    version INT DEFAULT 1         -- 乐观锁防脑裂
);
```

**高可用机制**：

- Coordinator 每 5 秒写心跳
- 看门狗每 30 秒扫描无心跳工作流
- 接管：读取已完成的节点，重新派发未完成的
- 乐观锁 + 令牌编号防脑裂

---

### 第三层：Session 动态管理

**核心能力**：

| 操作 | 说明 |
|------|------|
| **Create** | 创建独立 session，绑定 agent + user + task |
| **Fork** | 从某个检查点分叉，探索不同路径 |
| **Rollback** | 回滚到任意检查点，重新执行 |
| **Replay** | 确定性回放，调试用 |
| **Snapshot** | 保存完整状态快照 |

```python
class SessionManager:
    async def create(self, agent_id: str, user_id: str, task: Task) -> Session
    async def fork(self, session_id: str, from_step: int) -> Session
    async def rollback(self, session_id: str, to_step: int)
    async def replay(self, session_id: str) -> list[Step]
    async def snapshot(self, session_id: str) -> Snapshot
```

---

### 第四层：云端记忆库 (Cloud Memory Store)

**分层存储架构**：

```
┌──────────────────────────────────┐
│  HOT (Redis)                     │
│  - 当前会话消息 (最后50条)       │
│  - 速率限制计数器                │
│  - 熔断器状态                    │
│  - 延迟: < 1ms                   │
├──────────────────────────────────┤
│  WARM (PostgreSQL)                │
│  - 会话摘要 + 向量嵌入           │
│  - Agent 工作流状态              │
│  - 工具调用历史                  │
│  - 延迟: 5-20ms                  │
├──────────────────────────────────┤
│  COLD (S3 / MinIO)               │
│  - 完整对话归档                  │
│  - 调试日志                      │
│  - 延迟: 100-500ms               │
└──────────────────────────────────┘
```

**记忆类型（LangGraph 四层记忆架构）**：

| 类型 | 存储 | 用途 |
|------|------|------|
| **短期记忆** | Redis → PG | 当前对话上下文 |
| **语义记忆** | PG (pgvector) | "用户喜欢抒情歌"跨会话记住 |
| **经历记忆** | PG | Agent 以前干过的活，类似任务直接调 |
| **程序记忆** | PG | Agent 自我总结"这样做更快" |

**上下文压缩策略**：

```
传给 LLM 的上下文只包含：
┌──────────────────────────┐
│ 系统提示词 (缓存命中)     │ ← 不占 token
├──────────────────────────┤
│ 最近 N 轮对话 (原文)      │ ← 保持精准
├──────────────────────────┤
│ 历史摘要 (2-3句/轮)       │ ← 压缩
├──────────────────────────┤
│ 关键事实提取              │ ← 精炼
├──────────────────────────┤
│ 当前任务                  │ ← 重点
└──────────────────────────┘
```

---

### 第五层：可观测层 (Observability)

```
请求进入 → Span Tree:
├── agent.invoke                    (整体耗时)
│   ├── llm.call (deepseek-chat)    (模型调用)
│   │   ├── prompt_prepare          (提示词构建)
│   │   ├── api_request             (网络延迟)
│   │   └── response_parse          (结果解析)
│   ├── tool.execute (search)       (工具执行)
│   └── guardrail.check             (护栏校验)
├── memory.store                     (记忆写入)
└── session.checkpoint               (检查点保存)
```

**实现**：OpenTelemetry (AutoGen 同款方案)

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

@tracer.start_as_current_span("agent.invoke")
async def invoke(task: Task):
    ...
```

---

### 第六层：Actor 运行时

**设计模式**：每个 Agent 是一个 Actor，通过消息队列通信。

```
┌─────────┐  msg   ┌─────────┐  msg   ┌─────────┐
│ Agent 1 │ ──────▶│ Agent 2 │ ──────▶│ Agent 3 │
└─────────┘        └─────────┘        └─────────┘
     │                  │                  │
     └──────────────────┼──────────────────┘
                        │
              ┌─────────▼─────────┐
              │   Message Bus     │
              │   (asyncio Queue) │
              └───────────────────┘
```

**并发策略**：

```python
# asyncio.TaskGroup (Python 3.11+)，结构化并发
async with asyncio.TaskGroup() as tg:
    for agent in parallel_agents:
        tg.create_task(agent.execute(subtask))
# 所有 Agent 完成或任一失败后统一处理
```

---

## 可靠性机制

### 熔断器

每个模型提供者独立熔断：

```
状态机: CLOSED → OPEN → HALF_OPEN → CLOSED

触发条件:
- 连续失败 >= 5 次
- 或 60 秒内错误率 > 50%

恢复策略:
- OPEN 30 秒后进入 HALF_OPEN
- HALF_OPEN 发 3 个探针请求
- 2 个成功则 CLOSE
```

### 重试策略

| 错误 | 行为 |
|------|------|
| 429 (限流) | 读 Retry-After 头，等够时间再试 |
| 5xx | 指数退避 + 随机抖动，最多 3 次 |
| 超时 | 区分连接超时(快重试)和读超时(退避) |
| 4xx | 不重试（参数错误重试没用） |

### 幂等执行

每个操作带唯一 Key：`{workflow_id}:{step_id}:{tool_index}`

执行前查：干过没？干过了直接返回旧结果。

### 优雅降级

```
Level 0: 完美完成
Level 1: 模型降级 (GPT-4 挂了用 DeepSeek)
Level 2: 工具降级 (搜索挂了用备用引擎)
Level 3: 部分结果 (标记哪些步骤跳过了)
Level 4: 缓存结果 (标记"可能过时")
Level 5: 友好报错
```
