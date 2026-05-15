# 工具系统

## 设计原则

参考 Anthropic 的 "Poka-yoke"（防错）设计理念：

1. **工具描述比系统提示词更重要** —— 像给初级开发者的文档一样认真写
2. **让模型难以犯错** —— 参数设计天然防错
3. **验证在生成时就做** —— 不合规的工具调用在发给 LLM 之前就截断
4. **一次只做一件事** —— 不把多个功能塞进一个工具

---

## 工具定义 (JSON Schema)

```python
from pydantic import BaseModel, Field

class ToolDefinition(BaseModel):
    name: str
    description: str          # 越详细越好，像给初级开发者的文档
    parameters: dict           # JSON Schema
    handler: str               # 执行器标识
    is_expensive: bool = False
    requires_approval: bool = False
    timeout_ms: int = 30000
```

**描述模板（关键！）**：

```python
tool_description = """
{name}: {一句话功能}

## 用途
{2-3句话说明什么时候该用这个工具}

## 参数
- {param1} ({type}): {说明，含边界情况和默认行为}
- {param2} ({type}): {说明}

## 示例
{1-2个典型调用示例}

## 注意事项
- {与其他工具的边界}
- {常见误用场景}
- {返回值的含义}
"""
```

---

## 工具生命周期

```
注册 → 验证 → 加载 → 执行 → 记录

注册:    向 ToolRegistry 注册定义（只存 schema，轻量）
验证:    JSON Schema 校验
加载:    Agent 首次调用时懒加载实现（100个工具只加载用到的5个）
执行:    沙盒中执行，返回结果
记录:    写入 tool_executions 表（含幂等 Key）
```

---

## 注册工具

```python
from src.tools.registry import ToolRegistry, tool

registry = ToolRegistry()

# 方式一：装饰器
@tool(
    name="search_files",
    description="在项目中搜索文件...",
    parameters={...},
    requires_approval=False
)
async def search_files(query: str, path: str = "/") -> list[str]:
    ...

# 方式二：手动注册
registry.register(ToolDefinition(
    name="read_file",
    description="读取文件内容...",
    parameters={...},
    handler="builtin.read_file"
))
```

---

## 执行流程

```python
class ToolExecutor:
    async def execute(
        self,
        tool_name: str,
        args: dict,
        idempotency_key: str,
        session_id: str = None
    ) -> ToolResult:
        # 1. 幂等检查
        cached = await self.idempotency.check(idempotency_key)
        if cached:
            return cached

        # 2. 审批检查
        if self.requires_approval(tool_name):
            await self.approval_gate.wait(tool_name, args)

        # 3. 护栏检查（输入）
        if not self.guardrail.validate_input(tool_name, args):
            raise GuardrailRejected(f"Input validation failed for {tool_name}")

        # 4. 沙盒执行
        result = await self.sandbox.run(tool_name, args)

        # 5. 护栏检查（输出）
        if not self.guardrail.validate_output(tool_name, result):
            raise GuardrailRejected(f"Output validation failed")

        # 6. 记录 + 缓存
        await self.idempotency.store(idempotency_key, result)
        await self.record_execution(session_id, tool_name, args, result)

        return result
```

---

## 沙盒

### Subprocess 模式（默认）

```python
class SubprocessSandbox:
    """子进程隔离，轻量适合开发阶段"""
    async def run(self, tool_name: str, args: dict) -> Any:
        handler = self.registry.get_handler(tool_name)
        proc = await asyncio.create_subprocess_exec(
            "python", "-m", handler,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps(args).encode()),
            timeout=self.timeout_ms / 1000
        )
        return json.loads(stdout)
```

### Docker 模式（生产推荐）

```python
class DockerSandbox:
    """Docker 容器隔离，安全级别更高"""
    async def run(self, tool_name: str, args: dict) -> Any:
        container = await docker.containers.run(
            image=f"agent-sandbox:{tool_name}",
            command=["python", "-m", handler, json.dumps(args)],
            network_disabled=True,        # 禁止网络
            mem_limit="512m",
            cpu_limit=1.0,
            read_only=True,
            detach=True
        )
        result = await container.wait()
        return json.loads(await container.logs(stdout=True))
```

---

## 幂等执行

### 幂等Key 生成

```python
# HMAC 方案（防碰撞）
ik = hmac.new(
    key=workflow_id.encode(),
    msg=f"{step_id}:{tool_name}:{json.dumps(args, sort_keys=True)}".encode(),
    digestmod=hashlib.sha256
).hexdigest()

# 或简单方案
ik = f"{workflow_id}:{step_id}:{tool_call_index}"
```

### 缓存策略

```python
# Redis 存储
# Key: ik:{idempotency_key}
# Value: "completed:{json_result}" | "in_progress"
# TTL: 24h

async def check(self, key: str) -> Optional[ToolResult]:
    val = await redis.get(f"ik:{key}")
    if val and val.startswith("completed:"):
        return json.loads(val.split(":", 1)[1])
    if val == "in_progress":
        # 轮询等待（最多 30 秒）
        return await self._wait_for_completion(key, timeout=30)
    return None
```

---

## 复合工具（优化）

频繁连续调用的工具组合可以合并：

```python
@composite_tool(
    name="search_and_read",
    description="搜索文件并自动打开最相关的结果",
    tools=["search_files", "read_file"]
)
async def search_and_read(query: str, path: str = "/") -> dict:
    results = await search_files(query, path)
    if results:
        content = await read_file(results[0])
        return {"matches": results, "preview": content}
    return {"matches": results}
```

这消除了一个完整的 LLM → 工具 → LLM 循环，节省 2-5 秒。
