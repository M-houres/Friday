"""隔离沙盒 + 快照 —— 独立工作空间，可快照恢复"""

import asyncio
import json
import logging
import os
import shlex
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
import sys

logger = logging.getLogger(__name__)


@dataclass
class SandboxSnapshot:
    """沙盒快照"""
    snapshot_id: str
    workspace_path: str
    files: dict[str, str] = field(default_factory=dict)  # path → content
    env_vars: dict[str, str] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class IsolatedSandbox:
    """隔离沙盒 —— 独立工作空间 + 快照/恢复"""

    def __init__(
        self,
        workspace_root: str | None = None,
        max_disk_mb: int = 500,
        max_runtime_s: int = 300,
        sandbox_id: str | None = None,
    ):
        self.sandbox_id = sandbox_id or f"sandbox-{uuid.uuid4().hex[:8]}"
        self.workspace_root = workspace_root or tempfile.mkdtemp(prefix="friday-sandbox-")
        self.max_disk_mb = max_disk_mb
        self.max_runtime_s = max_runtime_s
        self._snapshots: dict[str, SandboxSnapshot] = {}
        self._lock = asyncio.Lock()
        self.owner_workflow_id: str | None = None
        self._snapshots_dir = os.path.join(self.workspace_root, ".snapshots")

        # 确保工作空间存在
        os.makedirs(self.workspace_root, exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "home"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "tmp"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace_root, "output"), exist_ok=True)
        os.makedirs(self._snapshots_dir, exist_ok=True)

    @property
    def home_dir(self) -> str:
        return os.path.join(self.workspace_root, "home")

    @property
    def tmp_dir(self) -> str:
        return os.path.join(self.workspace_root, "tmp")

    @property
    def output_dir(self) -> str:
        return os.path.join(self.workspace_root, "output")

    # ── 文件操作 ──

    async def read_file(self, path: str) -> str:
        full_path = self._resolve_path(path)
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {path}")
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    async def write_file(self, path: str, content: str):
        full_path = self._resolve_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)

    async def list_files(self, path: str = "") -> list[dict]:
        full_path = self._resolve_path(path)
        items = []
        for entry in os.scandir(full_path):
            items.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else 0,
            })
        return items

    async def delete_file(self, path: str):
        full_path = self._resolve_path(path)
        if os.path.isfile(full_path):
            os.remove(full_path)
        elif os.path.isdir(full_path):
            shutil.rmtree(full_path)

    async def file_exists(self, path: str) -> bool:
        return os.path.exists(self._resolve_path(path))

    # ── 命令执行 ──

    async def run_command(self, command: str | list[str], cwd: str = "", timeout_s: int | None = None) -> dict:
        cwd_path = self._resolve_path(cwd or ".")
        timeout = timeout_s or self.max_runtime_s

        args = self._coerce_command_args(command)
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "exit_code": proc.returncode or 0,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": -1, "stdout": "", "stderr": "Command timed out"}

    async def run_python(self, code: str, timeout_s: int = 30) -> dict:
        """在沙盒中执行 Python 代码（代码即动作）"""
        # 写入临时文件
        script_path = os.path.join(self.tmp_dir, f"script_{int(time.time())}.py")
        await self.write_file(script_path, code)

        relative_script = Path(script_path).resolve().relative_to(Path(self.workspace_root).resolve())
        result = await self.run_command(
            [sys.executable, str(relative_script)],
            cwd=".",
            timeout_s=timeout_s,
        )

        # 尝试解析 stdout 为 JSON
        try:
            result["parsed"] = json.loads(result["stdout"])
        except json.JSONDecodeError:
            result["parsed"] = None

        return result

    # ── 快照 ──

    async def snapshot(self, snapshot_id: str = "") -> str:
        """创建快照"""
        async with self._lock:
            snap_id = snapshot_id or f"snap_{int(time.time())}"
            files = {}
            for root, _, filenames in os.walk(self.workspace_root):
                if os.path.normpath(root).startswith(os.path.normpath(self._snapshots_dir)):
                    continue
                for filename in filenames:
                    filepath = os.path.join(root, filename)
                    relpath = os.path.relpath(filepath, self.workspace_root)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            files[relpath] = f.read()
                    except Exception:
                        pass  # 跳过二进制文件

            snapshot = SandboxSnapshot(
                snapshot_id=snap_id,
                workspace_path=self.workspace_root,
                files=files,
            )
            self._snapshots[snap_id] = snapshot
            self._write_snapshot(snapshot)
            logger.info(f"Sandbox snapshot created: {snap_id} ({len(files)} files)")
            return snap_id

    async def restore(self, snapshot_id: str):
        """恢复快照"""
        async with self._lock:
            snapshot = self._snapshots.get(snapshot_id)
            if snapshot is None:
                snapshot = self._read_snapshot(snapshot_id)
            if snapshot is None:
                raise ValueError(f"Snapshot not found: {snapshot_id}")

            # 清空当前工作空间
            for root, dirs, filenames in os.walk(self.workspace_root):
                for filename in filenames:
                    os.remove(os.path.join(root, filename))

            # 恢复文件
            for relpath, content in snapshot.files.items():
                full_path = os.path.join(self.workspace_root, relpath)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)

            logger.info(f"Sandbox restored from snapshot: {snapshot_id}")

    async def list_snapshots(self) -> list[dict]:
        snapshots: dict[str, SandboxSnapshot] = dict(self._snapshots)
        for path in Path(self._snapshots_dir).glob("*.json"):
            snapshot = self._read_snapshot(path.stem)
            if snapshot is not None:
                snapshots[snapshot.snapshot_id] = snapshot
        return [
            {"id": s.snapshot_id, "files": len(s.files), "created_at": s.created_at}
            for s in snapshots.values()
        ]

    async def delete_snapshot(self, snapshot_id: str):
        self._snapshots.pop(snapshot_id, None)
        snapshot_path = self._snapshot_path(snapshot_id)
        if os.path.exists(snapshot_path):
            os.remove(snapshot_path)

    # ── 磁盘检查 ──

    async def disk_usage(self) -> dict:
        total = 0
        file_count = 0
        for root, _, filenames in os.walk(self.workspace_root):
            for filename in filenames:
                filepath = os.path.join(root, filename)
                total += os.path.getsize(filepath)
                file_count += 1
        return {
            "used_bytes": total,
            "used_mb": round(total / (1024 * 1024), 2),
            "max_mb": self.max_disk_mb,
            "file_count": file_count,
            "percent": round(total / (self.max_disk_mb * 1024 * 1024) * 100, 1) if self.max_disk_mb > 0 else 0,
        }

    # ── 清理 ──

    async def cleanup(self):
        """清理工作空间"""
        if os.path.exists(self.workspace_root):
            shutil.rmtree(self.workspace_root)
        self._snapshots.clear()
        logger.info(f"Sandbox cleaned: {self.workspace_root}")

    def _resolve_path(self, path: str) -> str:
        """解析路径，防止沙盒逃逸"""
        if not path or path == ".":
            return self.workspace_root
        # 防止 ../ 逃逸
        full = os.path.normpath(os.path.join(self.workspace_root, path))
        if not full.startswith(os.path.normpath(self.workspace_root)):
            raise ValueError(f"Path escapes sandbox: {path}")
        return full

    @staticmethod
    def _coerce_command_args(command: str | list[str]) -> list[str]:
        if isinstance(command, list):
            args = [str(part) for part in command if str(part)]
        else:
            args = shlex.split(str(command), posix=False)
        if not args:
            raise ValueError("COMMAND_REQUIRED")
        return args

    def _snapshot_path(self, snapshot_id: str) -> str:
        return os.path.join(self._snapshots_dir, f"{snapshot_id}.json")

    def _write_snapshot(self, snapshot: SandboxSnapshot) -> None:
        with open(self._snapshot_path(snapshot.snapshot_id), "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "workspace_path": snapshot.workspace_path,
                    "files": snapshot.files,
                    "env_vars": snapshot.env_vars,
                    "created_at": snapshot.created_at,
                },
                fh,
                ensure_ascii=False,
            )

    def _read_snapshot(self, snapshot_id: str) -> SandboxSnapshot | None:
        snapshot_path = self._snapshot_path(snapshot_id)
        if not os.path.exists(snapshot_path):
            return None
        try:
            payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        snapshot = SandboxSnapshot(
            snapshot_id=str(payload.get("snapshot_id") or snapshot_id),
            workspace_path=str(payload.get("workspace_path") or self.workspace_root),
            files=dict(payload.get("files") or {}),
            env_vars=dict(payload.get("env_vars") or {}),
            created_at=float(payload.get("created_at") or time.time()),
        )
        self._snapshots[snapshot.snapshot_id] = snapshot
        return snapshot


class SandboxPool:
    """沙盒池 —— 预创建沙盒，快速分配"""

    def __init__(self, pool_size: int = 5, registry_root: str | None = None):
        self.pool_size = pool_size
        self._available: asyncio.Queue = asyncio.Queue()
        self._in_use: dict[str, IsolatedSandbox] = {}
        self._registry_root = Path(registry_root or (Path(tempfile.gettempdir()) / "friday-sandbox-registry"))
        self._registry_root.mkdir(parents=True, exist_ok=True)
        for _ in range(pool_size):
            self._available.put_nowait(IsolatedSandbox())

    async def acquire(self, workflow_id: str | None = None) -> IsolatedSandbox:
        if workflow_id and workflow_id in self._in_use:
            return self._in_use[workflow_id]

        restored = self._restore_registered_sandbox(workflow_id) if workflow_id else None
        if restored is not None:
            return restored

        sandbox = await self._available.get()
        sandbox.owner_workflow_id = workflow_id
        key = workflow_id or sandbox.sandbox_id
        self._in_use[key] = sandbox
        self._persist_registration(sandbox)
        return sandbox

    async def release(self, sandbox: IsolatedSandbox | None = None, workflow_id: str | None = None):
        target = sandbox
        if target is None and workflow_id:
            target = self._in_use.get(workflow_id) or self.get(workflow_id)
        if target is None:
            return

        key = target.owner_workflow_id or workflow_id or target.sandbox_id
        self._in_use.pop(key, None)
        self._clear_registration(target)
        # 重置沙盒
        try:
            await target.cleanup()
        except Exception:
            pass
        new_sandbox = IsolatedSandbox()
        await self._available.put(new_sandbox)

    def get(self, workflow_id: str) -> IsolatedSandbox | None:
        sandbox = self._in_use.get(workflow_id)
        if sandbox is not None:
            return sandbox
        for item in self._in_use.values():
            if item.sandbox_id == workflow_id:
                return item
        return self._restore_registered_sandbox(workflow_id)

    def _persist_registration(self, sandbox: IsolatedSandbox) -> None:
        record = {
            "sandbox_id": sandbox.sandbox_id,
            "owner_workflow_id": sandbox.owner_workflow_id,
            "workspace_root": sandbox.workspace_root,
            "max_disk_mb": sandbox.max_disk_mb,
            "max_runtime_s": sandbox.max_runtime_s,
        }
        self._registry_file(sandbox.sandbox_id).write_text(
            json.dumps(record, ensure_ascii=False),
            encoding="utf-8",
        )

    def _clear_registration(self, sandbox: IsolatedSandbox) -> None:
        registry_path = self._registry_file(sandbox.sandbox_id)
        if registry_path.exists():
            registry_path.unlink()

    def _registry_file(self, sandbox_id: str) -> Path:
        return self._registry_root / f"{sandbox_id}.json"

    def _load_registration(self, identifier: str) -> dict | None:
        direct = self._registry_file(identifier)
        candidates = [direct] if direct.exists() else list(self._registry_root.glob("*.json"))
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("sandbox_id") == identifier or payload.get("owner_workflow_id") == identifier:
                return payload
        return None

    def _restore_registered_sandbox(self, identifier: str | None) -> IsolatedSandbox | None:
        if not identifier:
            return None
        payload = self._load_registration(identifier)
        if payload is None:
            return None
        workspace_root = str(payload.get("workspace_root") or "")
        if not workspace_root or not os.path.exists(workspace_root):
            return None

        sandbox = IsolatedSandbox(
            workspace_root=workspace_root,
            max_disk_mb=int(payload.get("max_disk_mb") or 500),
            max_runtime_s=int(payload.get("max_runtime_s") or 300),
            sandbox_id=str(payload.get("sandbox_id") or None),
        )
        sandbox.owner_workflow_id = str(payload.get("owner_workflow_id") or "") or None
        self._in_use[sandbox.owner_workflow_id or sandbox.sandbox_id] = sandbox
        return sandbox


sandbox_pool = SandboxPool(pool_size=5)
