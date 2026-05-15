"""Prometheus 指标收集与导出"""

import asyncio
import logging
import time
from collections import defaultdict

from src.config import settings

logger = logging.getLogger(__name__)


class MetricsCollector:
    """轻量级 Prometheus 指标收集器 (无外部依赖)"""

    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list] = defaultdict(list)
        self._labels: dict[str, dict[str, str]] = {}
        self._start_time = time.time()

    def counter_inc(self, name: str, value: int = 1, labels: dict | None = None):
        key = self._metric_key(name, labels)
        self._counters[key] += value

    def gauge_set(self, name: str, value: float, labels: dict | None = None):
        key = self._metric_key(name, labels)
        self._gauges[key] = value

    def histogram_observe(self, name: str, value: float, labels: dict | None = None):
        key = self._metric_key(name, labels)
        self._histograms[key].append(value)
        if len(self._histograms[key]) > 10000:
            self._histograms[key] = self._histograms[key][-5000:]

    def snapshot(self) -> dict:
        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {k: list(v) for k, v in self._histograms.items()},
        }

    @staticmethod
    def _metric_key(name: str, labels: dict | None = None) -> str:
        if not labels:
            return name
        label_parts = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_parts}}}"

    def get_text_format(self) -> str:
        """输出 Prometheus 文本格式"""
        lines = []

        # UP 指标
        lines.append("# HELP friday_up Whether the service is up")
        lines.append("# TYPE friday_up gauge")
        lines.append("friday_up 1")

        # 运行时间
        uptime = time.time() - self._start_time
        lines.append("# HELP friday_uptime_seconds Service uptime in seconds")
        lines.append("# TYPE friday_uptime_seconds gauge")
        lines.append(f"friday_uptime_seconds {uptime:.1f}")

        # Counters
        for key, count in sorted(self._counters.items()):
            name, label_part = self._split_key(key)
            lines.append(f"# HELP {name} Auto-generated counter")
            lines.append(f"# TYPE {name} counter")
            if label_part:
                lines.append(f"{name}{{{label_part}}} {count}")
            else:
                lines.append(f"{name} {count}")

        # Gauges
        for key, value in sorted(self._gauges.items()):
            name, label_part = self._split_key(key)
            lines.append(f"# HELP {name} Auto-generated gauge")
            lines.append(f"# TYPE {name} gauge")
            if label_part:
                lines.append(f"{name}{{{label_part}}} {value}")
            else:
                lines.append(f"{name} {value}")

        # Histograms: summary as quantiles
        for key, values in sorted(self._histograms.items()):
            if not values:
                continue
            name, label_part = self._split_key(key)
            sorted_vals = sorted(values)
            count = len(sorted_vals)
            total = sum(sorted_vals)
            lines.append(f"# HELP {name} Auto-generated histogram")
            lines.append(f"# TYPE {name} summary")
            if label_part:
                lines.append(f"{name}_count{{{label_part}}} {count}")
                lines.append(f"{name}_sum{{{label_part}}} {total}")
            else:
                lines.append(f"{name}_count {count}")
                lines.append(f"{name}_sum {total}")

        return "\n".join(lines) + "\n"

    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        if "{" in key:
            name = key[:key.index("{")]
            label_part = key[key.index("{") + 1:key.rindex("}")]
            return name, label_part
        return key, ""

    async def _collect_system_metrics(self):
        """周期性收集系统指标"""
        while True:
            await asyncio.sleep(30)


metrics = MetricsCollector()
