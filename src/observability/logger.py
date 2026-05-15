"""结构化日志 —— JSON 格式、自动注入 trace_id、分级输出"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from src.config import settings


class StructuredFormatter(logging.Formatter):
    """JSON 结构化日志格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if hasattr(record, "trace_id"):
            log_entry["trace_id"] = record.trace_id
        if hasattr(record, "span_id"):
            log_entry["span_id"] = record.span_id
        if hasattr(record, "workflow_id"):
            log_entry["workflow_id"] = record.workflow_id
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id

        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        if hasattr(record, "extra_data") and record.extra_data:
            log_entry["data"] = record.extra_data

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """可读文本格式化器"""

    FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    def __init__(self):
        super().__init__(self.FORMAT, datefmt="%Y-%m-%d %H:%M:%S")


def setup_logging():
    """配置全局日志"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    # 清除已有 handler
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root.addHandler(handler)

    # 降低第三方库日志
    for lib in ("sqlalchemy", "aiosqlite", "httpx", "httpcore", "uvicorn"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    # uvicorn access log
    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.handlers.clear()
    uvicorn_logger.addHandler(handler)


class LoggerAdapter(logging.LoggerAdapter):
    """日志适配器 —— 自动注入 trace_id / workflow_id"""

    def __init__(self, logger: logging.Logger, extra: dict | None = None):
        super().__init__(logger, extra or {})

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.get("extra", {})
        extra.update(self.extra)
        kwargs["extra"] = extra
        return msg, kwargs
