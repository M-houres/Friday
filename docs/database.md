# 数据库设计

引擎：PostgreSQL 15+

---

## 表结构

### agent_definitions — Agent 定义

```sql
CREATE TABLE agent_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT 'deepseek-chat',
    strategy TEXT NOT NULL DEFAULT 'react',     -- react | plan_execute | reflection | orchestrator_workers
    tools TEXT[] NOT NULL DEFAULT '{}',
    config JSONB DEFAULT '{}',
    status TEXT DEFAULT 'idle',                  -- idle | running | degraded | dead
    stats JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_agents_status ON agent_definitions(status);
```

### sessions — 会话

```sql
CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID REFERENCES agent_definitions(id),
    user_id TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT DEFAULT 'created',               -- created | running | paused | completed | failed
    degradation_level INT DEFAULT 0,
    current_step INT DEFAULT 0,
    result JSONB,
    error TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_status ON sessions(status);
CREATE INDEX idx_sessions_agent ON sessions(agent_id);
CREATE INDEX idx_sessions_user ON sessions(user_id);
```

### session_steps — 会话步骤

```sql
CREATE TABLE session_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    type TEXT NOT NULL,                          -- think | tool_call | tool_result | observation
    content JSONB NOT NULL,
    model TEXT,
    tokens_used INT DEFAULT 0,
    latency_ms INT DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_steps_session ON session_steps(session_id, step_index);
```

### session_checkpoints — 检查点

```sql
CREATE TABLE session_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    state JSONB NOT NULL,                        -- 完整状态快照
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_checkpoints_session ON session_checkpoints(session_id);
```

### agent_workflows — 工作流

```sql
CREATE TABLE agent_workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planning',     -- planning | dispatching | executing | aggregating | completed | failed
    plan JSONB,                                  -- DAG 结构
    nodes_completed TEXT[] DEFAULT '{}',
    nodes_failed TEXT[] DEFAULT '{}',
    result JSONB,
    degradation_level INT DEFAULT 0,
    heartbeat_at TIMESTAMPTZ DEFAULT NOW(),
    coordinator_id TEXT,
    version INT DEFAULT 1,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    error TEXT
);

CREATE INDEX idx_workflows_status ON agent_workflows(status);
CREATE INDEX idx_workflows_heartbeat ON agent_workflows(heartbeat_at)
    WHERE status IN ('dispatching', 'executing', 'aggregating');
CREATE INDEX idx_workflows_user ON agent_workflows(user_id);
```

### workflow_nodes — 工作流节点

```sql
CREATE TABLE workflow_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES agent_workflows(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,                       -- DAG 中的节点标识
    agent_id UUID REFERENCES agent_definitions(id),
    task TEXT NOT NULL,
    dependencies TEXT[] DEFAULT '{}',
    status TEXT DEFAULT 'pending',               -- pending | running | completed | failed | skipped
    result JSONB,
    model TEXT,
    tokens_used INT DEFAULT 0,
    cost_usd DECIMAL(10,6) DEFAULT 0,
    attempts INT DEFAULT 0,
    max_attempts INT DEFAULT 3,
    priority INT DEFAULT 5,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error TEXT
);

CREATE INDEX idx_workflow_nodes ON workflow_nodes(workflow_id, node_id);
CREATE UNIQUE INDEX idx_workflow_nodes_epoch 
    ON workflow_nodes(workflow_id, node_id);
```

### session_memories — 会话记忆（温层）

```sql
CREATE TABLE session_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,
    summary TEXT,                                -- 会话摘要
    embedding vector(1536),                      -- pgvector 嵌入向量
    raw_messages JSONB,                          -- 原始消息（可能为空-已归档冷层）
    cold_storage_key TEXT,                       -- S3/MinIO key
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_memories_session ON session_memories(session_id);
CREATE INDEX idx_memories_user ON session_memories(user_id);
CREATE INDEX idx_memories_embedding ON session_memories
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

### long_term_memories — 长期记忆

```sql
CREATE TABLE long_term_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,                          -- semantic | episodic | procedural
    content JSONB NOT NULL,
    embedding vector(1536),
    importance DECIMAL(3,2) DEFAULT 0.5,
    access_count INT DEFAULT 0,
    last_accessed TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE INDEX idx_ltm_user_type ON long_term_memories(user_id, type);
CREATE INDEX idx_ltm_embedding ON long_term_memories
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

### tool_definitions — 工具定义

```sql
CREATE TABLE tool_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL,
    parameters JSONB NOT NULL,                   -- JSON Schema
    handler TEXT NOT NULL,                       -- 执行器标识
    is_expensive BOOLEAN DEFAULT FALSE,
    requires_approval BOOLEAN DEFAULT FALSE,
    timeout_ms INT DEFAULT 30000,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### tool_executions — 工具调用记录

```sql
CREATE TABLE tool_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    workflow_id UUID REFERENCES agent_workflows(id),
    step_id UUID REFERENCES session_steps(id),
    tool_name TEXT NOT NULL,
    input JSONB NOT NULL,
    output JSONB,
    status TEXT DEFAULT 'pending',               -- pending | running | completed | failed
    idempotency_key TEXT UNIQUE NOT NULL,
    latency_ms INT,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_tool_exec_session ON tool_executions(session_id);
CREATE INDEX idx_tool_exec_ik ON tool_executions(idempotency_key);
```

### circuit_breaker_state — 熔断器状态

```sql
CREATE TABLE circuit_breaker_state (
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'closed',        -- closed | open | half_open
    failure_count INT DEFAULT 0,
    last_failure TIMESTAMPTZ,
    last_success TIMESTAMPTZ,
    open_until TIMESTAMPTZ,
    half_open_count INT DEFAULT 0,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (provider, model)
);
```

### task_dead_letter_queue — 死信队列

```sql
CREATE TABLE task_dlq (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID REFERENCES agent_workflows(id),
    node_id TEXT,
    task_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    attempts INT DEFAULT 0,
    last_error TEXT,
    last_error_type TEXT,                        -- rate_limit | timeout | model_error | tool_failure | validation
    quarantine_reason TEXT,
    quarantined_at TIMESTAMPTZ DEFAULT NOW(),
    analyzed BOOLEAN DEFAULT FALSE,
    analysis_result JSONB,
    replayed_at TIMESTAMPTZ,
    replay_success BOOLEAN,
    archived BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_dlq_type_reason ON task_dlq(task_type, quarantine_reason);
CREATE INDEX idx_dlq_unanalyzed ON task_dlq(analyzed) WHERE NOT analyzed;
```

---

## Redis 数据结构

| Key | 类型 | 用途 |
|-----|------|------|
| `session:{id}:messages` | List | 当前会话消息（FIFO，限 50 条） |
| `session:{id}:meta` | Hash | 会话元数据 |
| `rate:{provider}:tokens` | String | 令牌桶当前 token 数 |
| `rate:{provider}:last_refill` | String | 上次补充时间戳 |
| `cb:{provider}:{model}:state` | String | 熔断器状态 |
| `cb:{provider}:{model}:failures` | List | 最近失败时间戳 |
| `ik:{idempotency_key}` | String | 幂等执行结果缓存 |
| `cache:exact:{model}:{hash}` | String | 精确匹配缓存 |
| `agent:pool:{agent_type}` | List | Agent 预热池 |
| `lock:workflow:{id}` | String | 工作流分布式锁 |

---

## 启用 pgvector

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```
