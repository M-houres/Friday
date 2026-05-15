"""沙盒执行环境"""

import asyncio
import logging
import shlex
import sys
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


class Sandbox:
    """沙盒抽象"""

    async def run(self, command: str | list[str], input_data: str | None = None, timeout_s: float = 30) -> tuple[int, str, str]:
        raise NotImplementedError


class SubprocessSandbox(Sandbox):
    """子进程沙盒 —— 轻量，适合开发"""

    async def run(self, command: str | list[str], input_data: str | None = None, timeout_s: float = 30) -> tuple[int, str, str]:
        args = _coerce_command_args(command)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_data.encode() if input_data else None),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return -1, "", "Command timed out"

        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class DockerSandbox(Sandbox):
    """Docker 容器沙盒 —— 隔离，适合生产"""

    def __init__(self, image: str = "agent-sandbox:latest"):
        self.image = image

    async def run(self, command: str | list[str], input_data: str | None = None, timeout_s: float = 30) -> tuple[int, str, str]:
        try:
            import aiodocker
        except ImportError:
            raise RuntimeError("aiodocker required for Docker sandbox. pip install aiodocker")

        args = _coerce_command_args(command)
        docker = aiodocker.Docker()
        container = await docker.containers.run(
            config={
                "Image": self.image,
                "Cmd": args,
                "NetworkDisabled": True,
                "HostConfig": {
                    "Memory": 512 * 1024 * 1024,
                    "CpuPeriod": 100000,
                    "CpuQuota": 100000,
                    "ReadonlyRootfs": True,
                },
            }
        )
        try:
            result = await asyncio.wait_for(container.wait(), timeout=timeout_s)
            logs = await container.logs(stdout=True, stderr=True)
            exit_code = result.get("StatusCode", -1)
        finally:
            await container.delete(force=True)
            await docker.close()

        return exit_code, "".join(logs), ""


def create_sandbox() -> Sandbox:
    """根据配置创建沙盒"""
    if settings.sandbox_type == "docker":
        return DockerSandbox()
    return SubprocessSandbox()


def _coerce_command_args(command: str | list[str]) -> list[str]:
    if isinstance(command, list):
        args = [str(part) for part in command if str(part)]
    else:
        args = shlex.split(str(command), posix=False)
    if not args:
        raise ValueError("COMMAND_REQUIRED")
    return args
