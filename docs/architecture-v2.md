# 星期五架构升级方案 v2

## 定位

星期五不是零代码平台，也不是“默认多 Agent 群聊”的实验框架。

升级后的目标是：

**一个可复用的 AI 应用底座。**

新项目只需要：

- 开发业务 Skill
- 开发业务前端页面
- 填项目级配置

底层的执行、状态、流式、隔离、鉴权、产物管理和可观测由框架统一复用。


## 核心原则

### 1. Workflow First, Agent Optional

默认先用确定性工作流，不把 LLM 当流程控制器。

- 业务流程型任务：走 `Pipeline` / `Graph Workflow`
- 开放探索型任务：走 `Agent DAG`
- 某一步确实需要开放推理时：走 `Agent Step`

Agent 是增强层，不是默认执行器。

### 2. 显式控制流

流程必须由框架明确表示：

- 步骤
- 依赖
- 条件分支
- 中断点
- 恢复点

不能把这些逻辑隐含在 prompt 或 `context` 里。

### 3. 显式状态

每个运行实例都维护一份强类型状态，不再靠散乱的 `dict` 传参。

步骤只允许：

- 读取当前状态
- 返回状态补丁
- 产生产物引用
- 发出事件

### 4. Artifact First

文件、报告、PPT、媒体结果不再直接写公共静态目录。

所有输出都进入统一产物服务，天然支持：

- `workflow_id` 归属
- `user_id` 归属
- `tenant_id` 归属
- 版本化
- 权限校验
- 过期和清理

### 5. Interrupt/Resume 是一等能力

审批、人工修订、补资料不是普通接口，而是标准运行状态。

框架需要原生支持：

- interrupt
- approve
- reject
- edit
- resume

### 6. 默认安全而不是默认开放

生产模式默认要求：

- 认证开启
- 流事件隔离
- 文件访问鉴权
- sandbox 归属绑定
- tool 级权限控制


## 执行模型

### 模式 A：Skill Pipeline

适用：

- PPT
- 法审
- 报告生成
- 文档加工
- 表单式业务流程

特点：

- 步骤固定
- 依赖固定
- 可预测
- 易测试
- 成本低

执行方式：

- 根据 Skill manifest 加载 workflow
- 逐步执行 step handler
- 维护统一 state
- 每一步写 checkpoint

### 模式 B：Graph Workflow

适用：

- 有条件分支
- 有循环重试
- 有审批回路
- 有人工补资料

特点：

- 节点和边显式定义
- 允许条件判断
- 允许进入 interrupt

执行方式：

- 运行图状态机
- 节点输出 state patch
- 运行内核决定下一节点

### 模式 C：Agent DAG

适用：

- 开放式复杂任务
- 研究类任务
- 任务结构不固定

特点：

- Planner 拆解 DAG
- 子任务可并行
- 允许多 Agent

限制：

- 不是默认模式
- 不适合固定业务 Skill 主链路

### 模式 D：Agent Step

适用：

- 某个工作流步骤需要开放推理
- 某个步骤需要工具选择、长推理、策略生成

特点：

- 只在局部节点启用 AgentRuntime
- 不让整条业务链退化成“到处猜工具”


## 新的核心模块

### 1. ExecutionRouter

职责：

- 根据请求和 Skill 元数据选择执行模式
- `skill_pipeline`
- `graph_workflow`
- `agent_dag`
- `session_agent`

输入：

- task
- skill match
- run config

输出：

- 具体 executor

### 2. SkillManifest

每个 Skill 应包含以下声明信息：

- name
- version
- description
- trigger
- input schema
- output schema
- workflow
- ui binding
- permissions
- artifact policy
- execution mode

Skill 不再只是“一个 Python 类 + 一堆工具”，而是一个完整的业务模块。

### 3. SkillWorkflowExecutor

职责：

- 按 workflow 顺序严格执行 handler
- 合并步骤输出到统一 state
- 写 checkpoint
- 发流式事件
- 处理中断和恢复

关键约束：

- 节点只能执行声明 handler
- 节点不能访问整个 Skill 的全部工具，除非显式声明

### 4. StateStore

职责：

- 保存运行状态
- 保存 step outputs
- 保存中断点
- 支持 resume / replay / rollback

建议模型：

- run metadata
- typed state snapshot
- step history
- event log

### 5. ArtifactService

职责：

- 保存产物
- 生成受控下载地址
- 记录归属和版本

接口建议：

- `put(run_id, kind, content | file_path, visibility)`
- `get(artifact_id, requester)`
- `list(run_id)`
- `delete_expired()`

### 6. EventStreamService

职责：

- 每个 run 独立事件通道
- SSE / WebSocket 均可挂载
- 支持回放最近事件

事件命名建议：

- `run.started`
- `step.started`
- `step.completed`
- `step.failed`
- `run.interrupted`
- `run.resumed`
- `artifact.ready`
- `run.completed`

### 7. PermissionPolicy

职责：

- 控制 Skill 可用能力
- 控制 tool 是否需要审批
- 控制文件与沙盒访问范围

粒度：

- project
- skill
- run
- step
- tool

### 8. TraceService

职责：

- trace 一个 run
- span 一个 step
- span 一个 llm call
- span 一个 tool call
- span 一个 guardrail


## 新的数据边界

### Run

一次完整执行实例。

字段建议：

- `run_id`
- `project_id`
- `skill_name`
- `execution_mode`
- `user_id`
- `tenant_id`
- `status`
- `input`
- `state`
- `result`

### Step

一次确定性步骤或 agent node 执行。

字段建议：

- `step_id`
- `run_id`
- `type`
- `status`
- `input`
- `output`
- `started_at`
- `completed_at`

### Artifact

一次运行中产生的交付物。

字段建议：

- `artifact_id`
- `run_id`
- `owner_user_id`
- `kind`
- `storage_key`
- `mime_type`
- `size`
- `visibility`


## 新的 Skill 开发模型

### Skill 目录建议

```text
skills/
  ppt/
    manifest.yaml
    skill.py
    prompts/
    ui/
      page.html
    tests/
  legal_review/
    manifest.yaml
    skill.py
    prompts/
    ui/
      page.html
    tests/
```

### manifest 示例

```yaml
name: ppt
version: 1.0.0
display_name: 一秒PPT
trigger:
  - PPT
  - 幻灯片
execution_mode: skill_pipeline
input_schema: ppt_request
output_schema: ppt_result
artifacts:
  - kind: pptx
    visibility: private
workflow:
  - id: research
    handler: research_topic
  - id: outline
    handler: generate_outline
    depends_on: [research]
  - id: slides
    handler: generate_all_slides
    depends_on: [outline]
  - id: assemble
    handler: assemble_pptx
    depends_on: [slides]
  - id: finalize
    handler: finalize_output
    depends_on: [assemble]
ui:
  route: /apps/ppt
  component: FridayPptPage
permissions:
  allow_artifacts: true
  allow_model_calls: true
  allow_agent_step: false
```


## 对现有架构的关键修正

### 当前问题 1：Skill workflow 只是元数据，不是真执行链

修正：

- 让 Skill workflow 进入正式执行器
- 去掉“节点起通用 Agent 猜工具”的默认路径

### 当前问题 2：上下文通过裸 `dict` 传递，且契约不稳定

修正：

- 使用统一 state 对象
- 每一步输出合并成 state patch

### 当前问题 3：SSE 全局广播

修正：

- 事件流按 `run_id` 或 `session_id` 隔离
- 订阅者只能看到自己授权的 run

### 当前问题 4：文件产物写公共静态目录

修正：

- 引入 ArtifactService
- 下载地址必须带授权校验

### 当前问题 5：sandbox_id 未绑定真实实例

修正：

- sandbox 与 run 绑定
- 一个 run 拿到固定 sandbox
- 生命周期跟随 run

### 当前问题 6：认证默认关闭

修正：

- dev 模式可关闭
- prod 模式必须开启


## 最终对外表述

升级后，星期五的准确定位应是：

**一个以确定性工作流为主、Agent 为增强层的 AI 应用底座。**

对内强调：

- 复用底层执行与隔离能力
- 新项目只开发业务 Skill 和业务前端

对外避免强调：

- 任意场景自动多 Agent
- 只写几行配置就能上线
- 完全零代码


## 设计取舍

### 为什么不把所有任务都走 Agent

- 成本高
- 延迟高
- 波动大
- 难测试
- 不适合业务流程

### 为什么要保留 Agent

- 开放探索任务仍然需要
- 局部复杂步骤仍然需要
- 可以作为高级能力而不是默认路径

### 为什么不继续扩张自研 durable engine

可以保留轻量 checkpoint runner，但不要无限堆积复杂逻辑。

企业级强需求出现后，再评估接入更成熟的 durable execution 方案。
