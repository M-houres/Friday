# API 文档

Base URL: `http://localhost:8000/api/v1`

下面只保留当前项目里对“垂直业务应用装配”最关键的接口。

## 1. 提交业务任务

### `POST /workflows`

请求：

```json
{
  "user_id": "demo-user",
  "task": "合同审查助手：请分析这份采购合同的付款与违约条款",
  "mode": "auto",
  "context": {},
  "project_id": "default",
  "page_id": "legal-briefing"
}
```

说明：

- 如果传了 `project_id + page_id`，优先走页面场景执行器
- 如果命中某个 Skill 的 trigger，优先走 `skill_pipeline`
- 否则走通用 Agent DAG

典型返回：

```json
{
  "workflow_id": "9b9d7f8b-...",
  "sandbox_id": "sbx-...",
  "status": "completed",
  "result": {
    "content": "...",
    "skill": "合同审查助手",
    "final_step": "deliver",
    "final_output": {
      "success": true,
      "data": {
        "summary": "..."
      }
    },
    "node_results": {}
  },
  "dags": {
    "nodes": [],
    "edges": []
  },
  "degradation_level": 0,
  "failed_nodes": []
}
```

前端如果是 Skill 页面，通常优先取：

- `result.final_output.data`

如果是页面场景，合并结果也在：

- `result.final_output.data`

## 2. 查询任务结果

### `GET /workflows/{workflow_id}`

返回数据库中的 workflow 记录。

可用于：

- 轮询状态
- 展示错误
- 查看持久化结果

常见状态：

- `planning`
- `dispatching`
- `executing`
- `aggregating`
- `completed`
- `failed`

## 3. 订阅实时进度

### `GET /stream/{workflow_id}`

SSE 事件流。

Skill pipeline 会发送：

- `start`
- `workflow-step`
- `finish`

`workflow-step` 关键字段：

```json
{
  "type": "workflow-step",
  "stepId": "process",
  "stepName": "执行业务流程",
  "stepIndex": 1,
  "totalSteps": 3,
  "status": "started",
  "workflowId": "..."
}
```

完成事件：

```json
{
  "type": "workflow-step",
  "stepId": "deliver",
  "status": "completed",
  "output": {},
  "workflowId": "..."
}
```

## 4. 获取 Skill 清单

### `GET /skills`

返回前端可消费的 Skill manifest 列表：

```json
{
  "skills": [
    {
      "name": "一秒PPT",
      "description": "...",
      "trigger": "PPT|幻灯片",
      "icon": "📊",
      "version": "2.0.0",
      "tools": 6,
      "project": "default",
      "route": "/ppt",
      "execution_mode": "skill_pipeline"
    }
  ]
}
```

### `GET /skills/{skill_name}`

返回单个 Skill 的完整定义：

- 工具 schema
- workflow
- output
- metadata

## 5. 获取应用分组

### `GET /projects`

返回当前应用分组列表：

```json
{
  "projects": [
    {
      "id": "default",
      "name": "Default",
      "description": "",
      "skills": ["一秒PPT", "法务审查"]
    }
  ]
}
```

### `GET /projects/{project_id}`

返回单个分组详情。

这里的 `project` 更适合理解为“应用装配分组”，不是复杂租户模型。

现在每个 project 还会包含：

- `home_route`
- `pages`
- `skill_manifests`

### `GET /projects/{project_id}/pages`

返回该产品下的页面清单。

## 6. 获取交付物元数据

### `GET /artifacts/{artifact_id}`

返回：

```json
{
  "artifact_id": "...",
  "workflow_id": "...",
  "filename": "report.md",
  "content_type": "text/markdown",
  "size_bytes": 12345,
  "download_url": "/api/v1/artifacts/.../download"
}
```

## 7. 下载交付物

### `GET /artifacts/{artifact_id}/download`

说明：

- 需要通过 owner 访问校验
- 生产模式下不应把产物直接暴露到公共静态目录

## 8. 健康检查

### `GET /health/live`

进程是否存活。

### `GET /health/ready`

依赖是否就绪：

```json
{
  "status": "ok",
  "database": true,
  "redis": true
}
```

## 9. 还有哪些接口

当前项目仍保留：

- `/agents`
- `/sessions`
- `/stats`
- `/memory/*`
- `/components/*`
- `/sandbox/*`
- `/metrics`

但如果你现在的目标是“快速开发新的垂直业务应用”，真正最值得优先用好的接口其实是：

1. `/workflows`
2. `/stream/{workflow_id}`
3. `/skills`
4. `/projects`
5. `/artifacts/*`
