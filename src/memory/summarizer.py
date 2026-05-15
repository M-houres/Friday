"""LLM 摘要生成器 —— 智能压缩对话历史，减少 token 消耗"""

import json
import logging
from typing import Optional

from src.models.base import Message
from src.models.router import model_router

logger = logging.getLogger(__name__)


class ConversationSummarizer:
    """对话摘要器 —— 用 LLM 生成精炼摘要"""

    SUMMARIZE_PROMPT = """将以下对话历史压缩为一段中文摘要（2-3句话），保留关键信息：
- 用户的核心意图和约束条件
- Agent 已执行的关键操作和结果
- 当前未完成的事项
- 用户表达的偏好和反馈

不要包含冗余的问候语和技术细节。"""

    def __init__(self, model: str = "deepseek-chat", max_summary_length: int = 300):
        self.model = model
        self.max_summary_length = max_summary_length

    async def summarize(self, messages: list[dict], current_task: str = "") -> str:
        """生成对话摘要"""
        if not messages:
            return ""

        text = self._messages_to_text(messages)
        if not text.strip():
            return ""

        system_msg = Message(role="system", content=self.SUMMARIZE_PROMPT)
        user_msg = Message(role="user", content=f"对话历史:\n{text[:4000]}")

        try:
            response = await model_router.chat(
                messages=[system_msg, user_msg],
                model=self.model,
                temperature=0.2,
                max_tokens=min(self.max_summary_length * 2, 512),
            )
            summary = response.content.strip()[:self.max_summary_length]
            logger.debug(f"Generated summary ({len(summary)} chars)")
            return summary
        except Exception as e:
            logger.warning(f"Summarization failed, using fallback: {e}")
            return self._fallback_summarize(text)

    async def summarize_session(self, session_messages: list[dict], user_prefs: dict | None = None) -> dict:
        """生成结构化摘要：事实 + 偏好 + 进展"""
        text = self._messages_to_text(session_messages)
        if not text.strip():
            return {"facts": [], "preferences": {}, "progress": ""}

        system_msg = Message(role="system", content="""分析以下对话，提取结构化信息。只返回 JSON。

格式：
{
  "facts": ["事实1", "事实2"],
  "preferences": {"key": "value"},
  "progress": "当前进展简述"
}""")

        user_msg = Message(role="user", content=f"对话内容:\n{text[:6000]}")

        try:
            response = await model_router.chat(
                messages=[system_msg, user_msg],
                model=self.model,
                temperature=0.1,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            return json.loads(response.content)
        except Exception as e:
            logger.warning(f"Structured summarization failed: {e}")
            return {
                "facts": [],
                "preferences": {},
                "progress": text[:200],
            }

    def _messages_to_text(self, messages: list[dict]) -> str:
        """将消息列表转为可摘要的文本"""
        lines = []
        for m in messages[-50:]:
            if not isinstance(m, dict):
                continue
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if isinstance(content, str):
                content = content[:300]
            elif isinstance(content, (list, dict)):
                content = json.dumps(content, ensure_ascii=False)[:300]
            else:
                content = str(content)[:300]
            if role == "tool":
                name = m.get("name", "")
                lines.append(f"[工具 {name}]: {content}")
            elif role == "assistant":
                lines.append(f"[AI]: {content}")
            elif role == "user":
                lines.append(f"[用户]: {content}")
            elif role == "system":
                if "[对话历史摘要]" in content or "上下文" in content:
                    lines.append(f"[上下文]: {content}")
        return "\n".join(lines)

    def _fallback_summarize(self, text: str) -> str:
        """规则兜底摘要"""
        sentences = text.replace("\n", " ").split("。")
        key_sentences = [s.strip() for s in sentences if len(s) > 10]
        return "。".join(key_sentences[:3]) + "。" if key_sentences else text[:200]
