# Agent 生命周期

## 状态机

```
        ┌─────────┐
        │  created │  定义创建，尚未就绪
        └────┬─────┘
             │ pre_warm()
             ▼
        ┌─────────┐
        │   idle   │  就绪，等待任务
        └────┬─────┘
             │ assign_task()
             ▼
        ┌─────────┐
   ┌───▶│ thinking │  模型推理中
   │    └────┬─────┘
   │        │ decide_action()
   │        ▼
   │    ┌─────────┐
   │    │  acting  │  执行工具中
   │    └────┬─────┘
   │        │ observe_result()
   │        ▼
   │    ┌─────────┐
   └────│observing│  观察结果，决定下一步
        └────┬─────┘
             │ task_complete()
             ▼
        ┌─────────┐
        │  done    │  任务完成
        └────┬─────┘
             │ (保留 N 分钟)
             ▼
        ┌─────────┐
        │  dead    │  回收
        └─────────┘
```

## 状态流转

### 正常路径

```
created → idle → thinking → acting → observing → thinking → ... → done → dead
```

### 异常路径

| 异常 | 流转 |
|------|------|
| 模型调用失败 | thinking → thinking (重试) 或 thinking → done (超过重试次数) |
| 工具执行失败 | acting → thinking (带错误信息，让模型修正) |
| 看门狗检测超时 | acting → thinking (重新执行上一步) |
| 用户取消 | 任意 → done |
| 优雅关机 | thinking/acting → 保存检查点 → dead |

## 预热机制

```python
class AgentPool:
    """Agent 实例池，预初始化避免冷启动"""

    def __init__(self, min_size=2, max_size=10):
        self.idle: asyncio.Queue = asyncio.Queue()
        self._fill_pool(min_size)

    def _fill_pool(self, n: int):
        for _ in range(n):
            agent = Agent()
            agent.pre_warm()  # 加载提示词、工具 schema、连 DB
            self.idle.put_nowait(agent)

    async def acquire(self) -> Agent:
        """获取一个预热好的 agent"""
        ...

    async def release(self, agent: Agent):
        """归还 agent 到池中"""
        agent.reset()  # 清除会话状态，但保留预热
        await self.idle.put(agent)
```

## Agent 内部循环 (ReAct 模式)

```python
class Agent:
    async def run(self, session: Session) -> FinalResult:
        while session.steps_remaining > 0:
            # 1. Think: 模型想下一步做什么
            thought = await self.brain.think(session.context)

            # 2. Act: 执行决策
            if thought.action == "call_tool":
                result = await self.hands.execute(thought.tool_call)
                session.add_observation(result)
            elif thought.action == "respond":
                session.add_response(thought.content)
                break
            elif thought.action == "delegate":
                await self.delegate_to(thought.target_agent, thought.task)

            # 3. Guardrail: 检查输出
            if not self.guardrail.check(session.last_output):
                session.rollback_last_step()

            # 4. Checkpoint: 存盘
            await session.checkpoint()

        return session.finalize()
```

## 销毁与恢复

### 销毁

```
1. 保存最终检查点到 session_checkpoints
2. 序列化会话摘要到 session_memories
3. 清理 Redis 热层
4. 释放 Agent 回池 (或彻底销毁)
```

### 恢复

```
1. 从 session_checkpoints 读取最新检查点
2. 重建 Agent 状态
3. 从 session_steps 恢复步进历史
4. 继续执行
```
