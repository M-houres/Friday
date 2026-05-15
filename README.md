# 星期五 (Friday)

> 面向垂直业务应用开发的 AI Agent 底座

## 定位

这不是零代码平台，也不应被理解为“一个后台承载很多无关业务”的重平台。

更准确的定位是：

- 复用同一套运行时、编排、鉴权、产物交付、流式事件、沙盒隔离能力
- 每做一个新业务应用，主要新增三类文件：
  - `skills/*.py`
  - `config/skills/*.json`
  - `static/*.html`
- 同时建议维护 `config/projects/*.json`，用于产品级页面导航、应用分组和展示元数据

一句话：**复用底座，快速装配新的垂直 AI 应用。**

## 当前执行模型

项目已经收口到 `Workflow First, Agent Optional`：

- 命中 Skill 的任务，优先走确定性 `skill_pipeline`
- 只有未命中 Skill 或需要通用拆解时，才走 Agent DAG
- 这比“所有任务都丢给多 Agent 猜怎么做”更稳定，也更适合商业化垂直场景

## 现在如何新增一个业务应用

### 方式一：直接生成骨架

```bash
python scripts/new_app.py contract_review ^
  --name "合同审查助手" ^
  --trigger "合同|法务|审查|协议" ^
  --description "面向合同审查场景的垂直应用骨架" ^
  --project-id legal_suite ^
  --artifact-kind markdown ^
  --create-project-config
```

生成后会得到：

```text
skills/contract_review_skill.py
static/contract-review.html
config/skills/contract_review.json
config/projects/legal_suite.json
```

然后你只需要做三件事：

1. 在 `skills/contract_review_skill.py` 里替换业务逻辑
2. 在 `static/contract-review.html` 里替换页面展示
3. 按需补产物交付、外部 API、鉴权策略

### 方式二：自己按约定新增文件

运行时会自动发现并装配：

- `skills/*.py`
- `config/skills/*.json`
- `static/*.html`
- `config/projects/*.json`

不需要改 `app.py`。

## 核心装配约定

### 1. Skill

Skill 是垂直业务主入口，负责：

- 触发词匹配
- 业务 workflow 声明
- 工具执行
- 前端交付结构收口

建议把 Skill 写成“确定性业务流水线”，不要默认依赖通用 Agent 自由发挥。

### 2. Skill Manifest

`config/skills/<app>.json` 用于声明：

- 路由
- 页面文件
- 执行模式
- 可见性
- 产物类型

示例：

```json
{
  "kind": "skill_manifest",
  "skill_name": "合同审查助手",
  "project_id": "legal_suite",
  "route": "/contract-review",
  "page": "contract-review.html",
  "execution_mode": "skill_pipeline",
  "visibility": "public",
  "artifact_kind": "markdown"
}
```

### 3. 前端页面

`static/*.html` 由 `app.py` 自动挂载。

页面不需要和 Skill 一一对应。更合理的关系是：

- 一个业务产品有多个页面
- 一个业务产品有多个 Skill
- 一个页面可以绑定一个或多个 Skill
- 首页、历史页、介绍页可以不直接绑定单个 Skill
- 某些页面还可以直接声明一个 `scenario`，由后端按顺序执行多个 Skill

页面默认可以调用：

- `POST /api/v1/workflows`
- `GET /api/v1/workflows/{workflow_id}`
- `GET /api/v1/stream/{workflow_id}`
- `GET /api/v1/artifacts/{artifact_id}`
- `GET /api/v1/artifacts/{artifact_id}/download`

## 关键能力

- 产品级多页面装配
- Skill 优先编排
- Agent DAG 回退路径
- 每个 workflow 独立 SSE 通道
- workflow 级 sandbox 归属
- 受控 artifact 下载，不再直接暴露静态文件
- 项目/应用 manifest 自动发现
- FastAPI API + 静态页面一体装配

## 启动

```bash
docker compose up -d
pip install -r requirements.txt
python -m src.db init
python app.py
```

默认入口：

- 面板：`/panel`
- API 文档：`/docs`
- 业务页面：按 manifest `route` 自动挂载

## 推荐开发路径

做一个新垂直应用时，优先按这个顺序推进：

1. 先生成骨架
2. 先把 Skill workflow 跑通
3. 再接文件交付、知识库、外部系统
4. 最后优化页面体验和实时反馈

## 相关文档

- [INTEGRATION.md](./INTEGRATION.md)
- [API.md](./API.md)
- [docs/architecture-v2.md](./docs/architecture-v2.md)
- [docs/implementation-plan-v2.md](./docs/implementation-plan-v2.md)
