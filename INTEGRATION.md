# 星期五 (Friday) — 垂直应用集成指南

## 目标

把星期五当成一个可复用底座，用来快速开发新的垂直 AI 业务应用。

不是零代码。
不是要求同一个运行时一定承载很多无关产品。
重点是：**同一套基础设施，快速装配不同垂直场景。**

## 最小接入单元

一个新业务应用，最少只需要三类文件：

```text
skills/<app>_skill.py
config/skills/<app>.json
static/<app>.html
```

可选：

```text
config/projects/<project>.json
```

这些文件会被 `app.py` 自动发现并挂载。

但更推荐的正式组织方式是：

- `project` 表示一个业务产品
- `page` 表示一个用户场景页面
- `skill` 表示一个后端能力模块

不是“一个 Skill = 一个产品页面”。

## 推荐接入方式

### 1. 生成应用骨架

```bash
python scripts/new_app.py industry_brief ^
  --name "行业简报助手" ^
  --trigger "简报|行业|周报|洞察" ^
  --description "自动生成行业简报与摘要" ^
  --project-id content_suite ^
  --artifact-kind markdown ^
  --create-project-config
```

### 2. 替换 Skill 中的业务逻辑

生成的 `skills/industry_brief_skill.py` 里已经有三步：

- `collect_requirements`
- `execute_business_flow`
- `finalize_output`

实际开发时重点改这两处：

1. `execute_business_flow`
   - 接模型
   - 接知识库
   - 接第三方 API
   - 接你自己的业务系统

2. `finalize_output`
   - 把结果整理成前端直接消费的数据结构
   - 如需文件交付，返回 artifact 的 `download_url`

### 3. 调整页面

生成的 `static/*.html` 已经能直接调用：

- `POST /api/v1/workflows`

并解析 skill pipeline 的最终结果。

你只需要根据业务改：

- 输入区
- 结果展示区
- 下载区
- 品牌风格

## 运行时如何装配

`app.py` 会自动执行四件事：

1. 扫描 `skills/*.py` 并 import
2. 读取 `config/skills/*.json`
3. 读取 `config/projects/*.json`
4. 优先按产品 manifest 把 `static/*.html` 挂到对应 `route`

因此新增业务应用时，原则上不应该再修改底座源码。

## 多页面和多 Skill 的推荐关系

推荐按下面的方式理解：

- 一个产品下面有多个页面
- 一个产品下面有多个 Skill
- 一个页面可以调用一个 Skill，也可以编排多个 Skill

例如一个“投标助手”产品：

- 页面
  - 首页
  - 文件解析页
  - 方案生成页
  - 历史记录页
- Skills
  - 文件解析
  - 风险审查
  - 方案生成
  - PPT导出

## 页面场景编排

如果一个页面需要串多个 Skill，不建议让前端自己逐个调用。

现在可以直接在 `config/projects/*.json` 的页面定义里声明：

```json
{
  "id": "legal-briefing",
  "route": "/legal-briefing",
  "page": "legal-briefing.html",
  "scenario": {
    "steps": [
      {"id": "legal_review", "skill": "法务审查"},
      {
        "id": "briefing_ppt",
        "skill": "一秒PPT",
        "task_template": "基于以下法务审查结论，生成一份管理层汇报PPT：\n\n{{result:legal_review}}"
      }
    ]
  }
}
```

前端提交时带上：

- `project_id`
- `page_id`

后端会自动按页面场景顺序执行多个 Skill，并返回合并后的结果。

## 业务 Skill 的建议写法

推荐把 Skill 当成“业务工作流”而不是“工具列表”：

- 用明确步骤表达业务链路
- 用 `depends_on` 或 workflow 顺序表达依赖
- 把可预测步骤写成确定性逻辑
- 把需要智能推理的局部步骤交给模型

这比完全依赖多 Agent 自由编排更稳定，也更好测试。

## 文件交付

如果应用需要导出报告、PPT、合同、音视频等交付物：

- 不要直接返回 `/static/output/...`
- 应使用 `src.artifacts.service` 创建 artifact
- 前端通过：
  - `GET /api/v1/artifacts/{artifact_id}`
  - `GET /api/v1/artifacts/{artifact_id}/download`
  访问

这样可以保留 owner 级访问控制。

## 鉴权和访问控制

当前项目已经支持：

- `auth_mode=none`
- `auth_mode=api_key`

在生产环境下：

- 不应继续使用无鉴权模式
- artifact 下载会校验 owner

如果你的新业务应用有更细粒度的数据权限，可以继续往 workflow、sandbox、memory 侧追加 owner 校验。

## 什么时候需要 project manifest

如果你只是快速做一个新页面，其实只配 `skill manifest` 就够了。

只有在这些场景下，建议补 `config/projects/*.json`：

- 需要把多个相关技能归到一个业务应用组
- 需要在门户页或后台按应用展示
- 需要保留应用说明、分组元数据

这里的 `project` 更适合理解为“应用装配分组”，不是复杂的平台租户系统。

## 实际开发边界

这套底座适合复用：

- workflow 编排
- Skill 路由
- SSE 流事件
- artifact 交付
- sandbox 隔离
- API 层
- 面板与观测能力

每个新业务通常仍需要针对性开发：

- Skill 业务逻辑
- 页面体验
- 数据结构
- 第三方系统集成
- 行业 prompt / 规则 / 模板

这正是当前项目应强调的方向。
