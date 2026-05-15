# 用星期五开发新应用：完整工作流

总共四步，每一步有提示词模板。你只需要填应用名称和核心功能，其余照抄。

---

## 步骤 1：定义应用

### 做什么

用一段话向 AI 描述你要做的应用。AI 会帮你拆解出子任务 DAG、列出需要哪些工具函数、设计前端页面长什么样、规划部署步骤。这一步产出的是"施工图纸"，后面三步按图施工。

### 提示词（直接复制，替换括号内容）

```
我要基于星期五框架开发一个新应用。

应用名称：[填名字，如"一秒PPT"]
应用类型：[网站 / 微信小程序 / API服务]
核心功能：[一句话描述用户能干嘛，如"用户输入主题，自动生成10页专业PPT并下载"]
目标用户：[谁用，如"上班族/学生/老师"]
付费模式：[免费/月付/按次付费]
参考竞品：[如有，列1-2个]

请帮我做以下四件事，详细输出：

1. 拆解任务DAG
   - 把核心功能拆成3-8个子任务
   - 标明依赖关系：哪些必须串行、哪些能并行
   - 用JSON输出节点和边

2. 列出工具函数清单
   - 每个工具的名称、用途描述、参数定义(JSON Schema)
   - 每个工具调什么外部API或做什么处理
   - 标明哪些工具可并行调用

3. 设计前端页面
   - 用户看到什么（输入框/按钮/结果区）
   - 交互流程（用户点哪里、看到什么反馈）
   - 加载状态怎么显示、错误怎么提示

4. 部署步骤
   - 需要哪些云服务（服务器/域名/存储）
   - docker-compose 怎么配
   - 环境变量需要填什么
```

### 产出物

一份完整的项目规划文档，包含 DAG 结构、工具清单、前端线框、部署方案。

---

## 步骤 2：写工具函数

### 做什么

把步骤 1 列出的工具函数，写成 Python 代码。每个工具就是一个 async 函数，用 `@tool` 装饰器注册到星期五框架。框架会自动把这些工具交给 Agent 调用。

### 提示词（复制后，把[工具清单]替换为步骤1的产出）

```
基于星期五框架的规范和以下工具定义，帮我写完整的可运行 Python 代码。

星期五框架的工具注册规范：
```python
from src.tools.registry import tool_registry, tool

@tool(
    name="工具名",
    description="工具用途，越详细越好，因为Agent靠描述决定什么时候调用它",
    parameters={
        "type": "object",
        "properties": {
            "参数名": {"type": "类型(string/number/boolean)", "description": "参数说明"}
        },
        "required": ["必填参数名"]
    }
)
async def 函数名(参数1: 类型, 参数2: 类型 = 默认值) -> dict:
    \"\"\"工具内部实现\"\"\"
    # 你的业务逻辑
    return {"key": value}
```

星期五框架调用 LLM 的方式（如果工具需要调模型）：
```python
from src.models.router import model_router
from src.models.base import Message
response = await model_router.chat(
    messages=[Message(role="system", content="系统提示词"), Message(role="user", content="用户输入")],
    temperature=0.7
)
# 框架自动处理熔断、重试、降级、缓存
```

需要写的工具清单：
[把步骤1产出的工具清单贴在这里]

要求：
- 每个工具完整实现业务逻辑
- 处理异常情况（API超时、返回格式错误等）
- 返回统一的 dict 格式 {"success": true/false, "data": ..., "error": "..."}
- 代码可直接运行，不要省略任何 import
```

### 产出物

一个 `tools.py` 文件，包含所有工具函数。

---

## 步骤 3：写前端页面

### 做什么

写用户看到的所有界面。最简单就是一个 index.html，包含输入框、提交按钮、结果展示区。如果做小程序就用微信开发者工具，组件逻辑一样。

### 提示词

```
帮我写一个前端页面，基于星期五框架的 API 接口。

星期五框架的核心 API：
  提交任务：POST /api/v1/workflows
  请求体：{"task": "用户输入的任务描述", "user_id": "用户ID", "mode": "auto"}
  响应体：{"workflow_id": "xxx", "result": {"content": "文本结果", "file_url": "下载链接"}, "degradation_level": 0}

应用名称：[填步骤1的名字]
核心功能：[填步骤1的功能描述]
前端布局：[把步骤1产出的布局描述贴在这里]
技术栈：纯 HTML/CSS/JS（不要框架）

页面要求：
- 一个输入区域（文本框/上传按钮）
- 一个提交按钮（点击后显示加载状态，防止重复点击）
- 一个结果展示区（文字结果、图片结果、下载链接、视频播放器等）
- 加载中显示动画或文字提示
- 出错显示红色错误信息
- 适配手机屏幕（响应式设计）
- 界面风格：[简洁/科技感/可爱/商务]

打包为单个 index.html 文件。
```

### 产出物

一个 `index.html` 文件，浏览器直接打开就能用。

---

## 步骤 4：部署上线

### 做什么

把星期五框架 + 你的工具 + 前端页面打包成 Docker，部署到云服务器。用户通过域名访问。

### 提示词

```
帮我写部署配置文件，部署以下应用：

项目文件结构：
  星期五/          （框架源码目录）
  tools.py         （步骤2产出的工具文件）
  static/index.html（步骤3产出的前端文件）
  main.py          （启动入口，内容如下）

main.py 内容：
```python
import sys; sys.path.insert(0, "星期五")
from src.main import app
import tools  # 注册所有工具
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="static", html=True), name="static")
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

部署环境：
  服务器：[阿里云/腾讯云/AWS，几核几G]
  操作系统：Ubuntu 22.04
  需要：PostgreSQL + Redis + Nginx 反代 + HTTPS证书

请帮我写：
1. requirements.txt（星期五已有的依赖 + 新项目需要的）
2. Dockerfile（多阶段构建，最终镜像尽量小）
3. docker-compose.yml（包含 postgres、redis、app 三个服务）
4. Nginx 配置文件（反代到8000端口、支持HTTPS、开启gzip、设置超时）
5. .env.example（列出所有需要填的环境变量）
6. 部署命令（从 git clone 到 https 可访问的完整步骤）
```

### 产出物

一套部署配置文件和上线命令。

---

## 速查卡

| 步骤 | 做什么 | 你给AI什么 | AI产出什么 | 耗时 |
|------|--------|-----------|-----------|------|
| 1 | 定义 | 填应用名和功能 | DAG+工具清单+布局+部署方案 | 10分钟 |
| 2 | 写工具 | 贴工具清单 | tools.py | 30分钟 |
| 3 | 写前端 | 贴布局描述 | index.html | 1小时 |
| 4 | 部署 | 贴服务器信息 | 整套部署配置 | 10分钟 |

**四步走完，应用上线。框架内部的调度、容错、缓存、监控你完全不用管。**
