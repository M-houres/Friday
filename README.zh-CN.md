# Friday

> 用一套可复用的工作流、工具、产物和运营底座，构建垂直 AI 应用。

[English](./README.md) | [简体中文](./README.zh-CN.md)

Friday 是一个开源框架，用来构建**复杂但有边界的 AI 产品**，例如法务助手、报告生成器、内部 Copilot、审批驱动的工作流系统、多页面 AI 工作台等业务型应用。

它**不是**零代码平台，也**不是**追求无限自治的通用超级 Agent。它的设计目标很明确：帮助团队更快交付可上线、可运营、可扩展的 AI 产品。

- 复用运行时、鉴权、计费、工作流、产物、沙盒等基础设施
- 优先走确定性的 `Skill` 工作流
- Agent 编排作为回退路径，而不是所有任务的默认执行方式
- 内置项目页面配置和运营后台能力

## 为什么做 Friday

大多数 AI 应用团队，并不想每做一个新产品就重建一遍这些基础设施：

- 登录与账户初始化
- 模型路由与流式输出
- 工作流执行与状态跟踪
- 产物生成与受控下载
- 审批节点与异步任务
- 管理后台与运营配置能力

Friday 的目标，就是把这些做成一层共享底座。这样你做一个新应用时，通常只需要新增：

- 一个新的 `Skill`
- 一个新的前端页面
- 一份新的 manifest / 配置

## 它适合做什么

Friday 特别适合：

- 有明确业务边界的多步骤 AI 工作流
- 需要生成交付物的 AI 产品，例如报告、Markdown、JSON、PPT 或文件
- 带多个页面、任务历史、运营后台的内部工作台
- 有人工参与的 AI 产品，例如审批、重试、异步执行
- 法务、内容、知识、分析、运营等垂直领域应用

Friday 相对不适合：

- 高自治、开放式、持续运行数小时或数天的通用 Agent
- 超大规模多 Agent 团队协作模拟
- ERP 级强事务核心系统
- 高实时协同系统中仅作为一个小子模块存在的 AI 能力

## 架构方向

Friday 遵循一个很明确的原则：

**Workflow First, Agent Optional**

- 如果任务命中了已知业务 `Skill`，优先执行确定性的 `skill_pipeline`
- 如果任务没有命中，或者确实需要更通用的拆解，再回退到 Agent DAG 编排

这样做的好处是：生产环境会比“所有请求都丢给开放式 Agent 自由发挥”更稳定、更可控。

## 核心能力

- Skill 优先执行模型
- Agent DAG 回退编排
- FastAPI API 与静态页面一体挂载
- 按 workflow 隔离的 SSE 流式通道
- 带 workflow 归属的 sandbox 执行环境
- 受控 artifact 生成与下载
- 项目与 Skill manifest 自动发现
- 登录、账户初始化、限流、鉴权模式
- 异步任务、审批流、运营接口
- 模型路由、重试与熔断

## 项目结构

```text
app.py                    # 应用入口与自动发现启动
src/                      # 核心运行时、API、编排、工具、模型
skills/                   # 垂直业务 Skill
static/                   # 前端页面
config/skills/            # Skill manifest
config/projects/          # 项目/页面 manifest
scripts/new_app.py        # 新垂直应用脚手架
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动基础设施

```bash
docker compose up -d
```

### 3. 配置环境变量

```bash
copy config/.env.example config/.env
```

然后填入你实际使用的模型与运行时配置。

### 4. 启动应用

```bash
python app.py
```

默认入口：

- 面板：`http://localhost:8000/panel`
- API 文档：`http://localhost:8000/docs`
- 业务页面：根据项目和 Skill manifest 自动挂载

## 创建一个新的 AI 应用

先生成脚手架：

```bash
python scripts/new_app.py contract_review ^
  --name "合同审查助手" ^
  --trigger "合同|法务|审查|协议" ^
  --description "面向合同审查场景的垂直应用骨架" ^
  --project-id legal_suite ^
  --artifact-kind markdown ^
  --create-project-config
```

会生成：

```text
skills/contract_review_skill.py
static/contract-review.html
config/skills/contract_review.json
config/projects/legal_suite.json
```

实际开发时，大多数新应用只需要做三件事：

1. 在 `skills/*.py` 里实现业务逻辑
2. 在 `static/*.html` 里实现页面场景
3. 在 `config/projects` 和 `config/skills` 里接好项目与 Skill 元数据

## Friday 如何扩展

### Skill

`Skill` 是垂直业务主入口，通常负责：

- 触发词匹配
- workflow 步骤定义
- 工具执行
- 面向前端的结果收口

### Skill Manifest

`config/skills/<app>.json` 用于声明路由、页面、执行模式、可见性和产物类型。

### Project Manifest

`config/projects/*.json` 用于描述产品级页面、导航、分组以及页面级 scenario。

### Frontend Page

`static/*.html` 页面会自动挂载。一个页面可以绑定一个 Skill、多个 Skill，或者直接绑定一个 scenario 流程。

## 开发方式

推荐按这个顺序交付一个新的垂直 AI 产品：

1. 先生成应用骨架
2. 先把 Skill workflow 跑通
3. 再接入 artifact、外部 API 或知识源
4. 再优化页面体验和流式反馈
5. 最后按需要补审批、计费和运营能力

## 当前状态

这个项目已经可以作为**第一版具备生产能力的 AI 应用底座**使用，尤其适合垂直、工作流驱动的业务型产品。

最近完成的加固包括：

- artifact 元数据持久化
- sandbox 注册与恢复
- 基于 Redis 的跨进程 SSE 桥接，并保留本地回退
- 仅在冷启动时做基线 DB bootstrap
- 全量回归测试通过

## 文档

- [API.md](./API.md)
- [INTEGRATION.md](./INTEGRATION.md)
- [ARCHITECTURE.md](./ARCHITECTURE.md)
- [docs/architecture-v2.md](./docs/architecture-v2.md)
- [docs/implementation-plan-v2.md](./docs/implementation-plan-v2.md)

## License

MIT
