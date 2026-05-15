# 星期五升级实施清单 v2

## 目标

把当前项目从“Agent 导向原型”升级为“Workflow 优先的可复用 AI 应用底座”。

本清单按：

- P0：必须先修，不修无法作为底座
- P1：可复用化升级
- P2：高级能力

来安排。


## P0：执行语义与安全底线

### P0.1 Skill workflow 真执行

目标：

- 命中 Skill 时，不再走“通用 Agent 猜工具”
- 按 Skill 声明的步骤和依赖直接执行

涉及文件：

- `src/tools/skill.py`
- `src/orchestration/planner.py`
- `src/orchestration/coordinator.py`
- `src/orchestration/dag.py`

具体改造：

- 新增 `SkillWorkflowExecutor`
- `Planner` 对 Skill 任务返回 `execution_mode = skill_pipeline`
- `Coordinator` 根据 `execution_mode` 路由执行器
- 每个节点只执行声明 handler

验收：

- PPT Skill 按 `research -> outline -> slides -> assemble -> certify -> finalize` 稳定执行
- 法务 Skill 同理

### P0.2 统一状态模型

目标：

- 步骤间结果稳定传递
- 去掉散乱 `context` 契约

涉及文件：

- `src/tools/skill.py`
- `src/session/manager.py`
- `src/orchestration/workflow_engine.py`

具体改造：

- 定义 `RunState`
- 每一步返回 `state_patch`
- 执行器合并状态
- 保存 step output 和 state snapshot

验收：

- 每个 step 都能读取前置步骤输出
- 不再依赖“tool 名 / workflow id 混用”的脆弱契约

### P0.3 SSE 隔离

目标：

- 每个 run 的事件只发给对应订阅者

涉及文件：

- `src/api/stream.py`
- `src/api/routes.py`

具体改造：

- `FridayStream` 改成按 `channel_id/run_id` 维护队列
- `GET /stream/{workflow_id}` 真正只返回对应 workflow 事件
- 加鉴权和取消订阅清理

验收：

- 两个并发 workflow 的前端不会串消息

### P0.4 ArtifactService 替代公共 static 输出

目标：

- 文件产物受控访问

涉及文件：

- `app.py`
- `skills/ppt_skill.py`
- `skills/legal_skill.py`
- `src/api/routes.py`
- 新增 `src/artifacts/service.py`

具体改造：

- 业务 Skill 不再直接给公开 `download_url`
- 统一上传到 artifact store
- 通过 `artifact_id` 下载
- 下载接口检查用户权限

验收：

- 法务报告和 PPT 结果不再裸露在 `/static/output`

### P0.5 sandbox ownership

目标：

- 一个 run 对应一个真实 sandbox

涉及文件：

- `src/tools/isolated_sandbox.py`
- `src/api/routes.py`

具体改造：

- `sandbox_pool.acquire(run_id)` 绑定固定实例
- `sandbox_id` 不再是假的路径参数
- run 结束统一释放

验收：

- 同一 run 可以稳定查看自己的文件与快照
- 不同 run 之间不会复用同一实例视图

### P0.6 生产安全默认值

目标：

- 开发模式宽松，生产模式安全

涉及文件：

- `src/config.py`
- `src/api/auth.py`
- `src/main.py`

具体改造：

- 增加 `environment = dev|prod`
- prod 模式下 auth 不能为空
- 面板和敏感端点走权限校验

验收：

- prod 配置缺失时服务拒绝启动


## P1：可复用化改造

### P1.1 Skill 自动发现

目标：

- 不再手工 import Skill

涉及文件：

- `app.py`
- `skills/`
- `src/tools/skill.py`

具体改造：

- 扫描 `skills/*/manifest.yaml`
- 自动注册 Skill
- 自动加载 handler 模块

验收：

- 新增一个 Skill 目录，不改核心入口即可被识别

### P1.2 项目注册层

目标：

- 支持一个底座承载多个业务项目

涉及文件：

- 新增 `src/projects/registry.py`
- 新增 `config/projects/`

具体改造：

- 定义 `project_id`
- 项目绑定 skill 集合、页面路由、权限策略

验收：

- 同一个 Friday 实例可挂多个业务应用

### P1.3 UI Binding 规范化

目标：

- 页面入口和组件绑定由 manifest 驱动

涉及文件：

- `src/api/component_registry.py`
- `app.py`
- `static/`

具体改造：

- Skill manifest 声明 route / page / component
- 启动时自动挂载页面和组件映射

验收：

- 不再在 `app.py` 手工写 `/ppt`、`/legal`

### P1.4 Tool Permission Model

目标：

- 不同 Skill、不同步骤有不同能力边界

涉及文件：

- `src/core/guardrail.py`
- `src/core/guardrail_chain.py`
- `src/tools/registry.py`
- `src/tools/skill.py`

具体改造：

- tool 增加 permission scope
- step 增加 approval policy
- handler 触发时先做能力检查

验收：

- 高风险工具不能被默认任意调用


## P2：高级能力

### P2.1 Graph Workflow

目标：

- 支持条件分支、循环、审批回路

涉及文件：

- `src/orchestration/workflow_engine.py`
- 新增 `src/orchestration/graph_executor.py`

### P2.2 Interrupt / Resume

目标：

- 审批、人工修订、补资料成为正式运行状态

涉及文件：

- `src/api/routes.py`
- `src/orchestration/workflow_engine.py`
- `src/api/stream.py`

### P2.3 Agent Step

目标：

- 某个节点局部启用 AgentRuntime

涉及文件：

- `src/core/agent_runtime.py`
- `src/orchestration/coordinator.py`
- `src/tools/skill.py`

### P2.4 Trace 标准化

目标：

- run / step / llm / tool 全链路 span

涉及文件：

- `src/observability/tracing.py`
- `src/observability/logger.py`


## 推荐实施顺序

### 第一轮

- P0.1 Skill workflow 真执行
- P0.2 统一状态模型
- P0.3 SSE 隔离

这是框架是否可靠的分水岭。

### 第二轮

- P0.4 ArtifactService
- P0.5 sandbox ownership
- P0.6 生产安全默认值

这是框架是否能商用复用的分水岭。

### 第三轮

- P1.1 Skill 自动发现
- P1.2 项目注册层
- P1.3 UI Binding 规范化

这是框架是否方便迁移新项目的分水岭。

### 第四轮

- P1.4 Tool Permission Model
- P2 全部

这是长期演进和高级场景能力。


## 当前模块去留建议

### 保留并强化

- `src/core/agent_runtime.py`
- `src/orchestration/dag.py`
- `src/orchestration/workflow_engine.py`
- `src/session/*`
- `src/observability/*`

### 重构

- `src/tools/skill.py`
- `src/orchestration/coordinator.py`
- `src/api/stream.py`
- `src/tools/isolated_sandbox.py`
- `app.py`

### 降级为可选高级能力

- `src/orchestration/jit.py`
- `src/core/interleaved.py`
- `src/orchestration/durable.py`

这些能力先不要继续前置到主链路，先把执行语义和安全边界打稳。


## 验收标准

升级完成后，至少满足以下结果：

- 新增一个业务 Skill 时，不需要改框架核心编排逻辑
- Skill 多步骤任务稳定执行，前后文正确传递
- 新增一个业务页面时，不需要手工写专属路由
- 两个用户同时运行任务时，流事件和产物不串
- 敏感文件不能通过公共静态目录直接访问
- 生产模式未配置认证时不能启动


## 建议的下一步

直接进入第一轮代码改造：

1. 重构 `src/tools/skill.py`
2. 在 `src/orchestration/` 中新增 `SkillWorkflowExecutor`
3. 修改 `Coordinator` 的执行路由
4. 改造 `src/api/stream.py` 的 run 级通道

这四步完成后，框架的方向就从“概念正确”变成“底层语义正确”。
