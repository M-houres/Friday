"""可观测层 —— 结构化日志 + OpenTelemetry 追踪"""

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from src.config import settings


def setup_logging():
    """配置结构化日志"""
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format == "json":
        try:
            import structlog
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(level),
                processors=[
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.processors.add_log_level,
                    structlog.processors.JSONRenderer(),
                ],
            )
        except ImportError:
            pass

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class Tracer:
    """追踪器 —— 优先使用 OpenTelemetry，否则使用轻量实现"""

    def __init__(self, name: str = "friday"):
        self.name = name
        self._spans: list[dict] = []
        self._otel_tracer = None

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            provider = TracerProvider()
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

            otlp_endpoint = getattr(settings, 'otel_exporter_otlp_endpoint', '')
            if otlp_endpoint:
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                )

            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer(name)
            logging.getLogger(__name__).info("OpenTelemetry tracing enabled")

        except ImportError:
            logging.getLogger(__name__).info("OpenTelemetry not installed, using lightweight tracer")
        except Exception as e:
            logging.getLogger(__name__).warning(f"OpenTelemetry init failed: {e}")

    @asynccontextmanager
    async def span(self, operation: str, **attributes):
        start = time.monotonic()
        span_ctx = {"operation": operation, "attributes": attributes, "start": start}

        otel_span = None
        if self._otel_tracer:
            otel_span = self._otel_tracer.start_span(operation)
            for k, v in attributes.items():
                otel_span.set_attribute(k, v)

        try:
            yield
        except Exception as e:
            span_ctx["error"] = str(e)
            if otel_span:
                otel_span.record_exception(e)
                otel_span.set_status(
                    type(self).__module__, "error", {"description": str(e)}
                )
            raise
        finally:
            elapsed = (time.monotonic() - start) * 1000
            span_ctx["duration_ms"] = elapsed
            self._spans.append(span_ctx)
            logging.getLogger("trace").info(
                f"{operation} | {elapsed:.1f}ms | {attributes}"
            )

            if otel_span:
                otel_span.set_attribute("duration_ms", elapsed)
                otel_span.end()

    def get_recent_spans(self, limit: int = 50) -> list[dict]:
        return self._spans[-limit:]


tracer = Tracer()
