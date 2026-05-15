"""Artifact service —— 受控管理工作流产物和下载访问。"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException


class ArtifactService:
    def __init__(self, root_dir: str | None = None):
        self.root_dir = Path(root_dir or (Path(__file__).resolve().parent.parent.parent / "data" / "artifacts"))
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._records_dir = self.root_dir / "_records"
        self._records_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict] = {}

    def create_from_file(
        self,
        *,
        workflow_id: str,
        filename: str,
        source_path: str,
        owner_user_id: str = "default",
        content_type: str | None = None,
    ) -> dict:
        artifact_id = uuid.uuid4().hex
        workflow_dir = self.root_dir / workflow_id
        workflow_dir.mkdir(parents=True, exist_ok=True)

        safe_name = f"{artifact_id}_{Path(filename).name}"
        target_path = workflow_dir / safe_name
        shutil.copy2(source_path, target_path)

        content_type = content_type or mimetypes.guess_type(str(target_path))[0] or "application/octet-stream"
        size_bytes = target_path.stat().st_size

        record = {
            "artifact_id": artifact_id,
            "workflow_id": workflow_id,
            "owner_user_id": owner_user_id,
            "filename": Path(filename).name,
            "path": str(target_path),
            "content_type": content_type,
            "size_bytes": size_bytes,
            "download_url": f"/api/v1/artifacts/{artifact_id}/download",
        }
        self._records[artifact_id] = record
        self._write_record(record)
        return dict(record)

    def get(self, artifact_id: str) -> dict | None:
        record = self._records.get(artifact_id) or self._read_record(artifact_id)
        if record is None:
            return None
        if not os.path.exists(record["path"]):
            return None
        self._records[artifact_id] = record
        return dict(record)

    def require(self, artifact_id: str) -> dict:
        record = self.get(artifact_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Artifact not found")
        return record

    def list_by_workflow(self, workflow_id: str) -> list[dict]:
        records: list[dict] = []
        seen: set[str] = set()

        for record in self._records.values():
            if record["workflow_id"] == workflow_id and os.path.exists(record["path"]):
                records.append(dict(record))
                seen.add(record["artifact_id"])

        for path in self._records_dir.glob("*.json"):
            artifact_id = path.stem
            if artifact_id in seen:
                continue
            record = self._read_record(artifact_id)
            if record is None:
                continue
            if record["workflow_id"] == workflow_id and os.path.exists(record["path"]):
                self._records[artifact_id] = record
                records.append(dict(record))

        return records

    def _record_path(self, artifact_id: str) -> Path:
        return self._records_dir / f"{artifact_id}.json"

    def _write_record(self, record: dict) -> None:
        self._record_path(str(record["artifact_id"])).write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_record(self, artifact_id: str) -> dict | None:
        record_path = self._record_path(artifact_id)
        if not record_path.exists():
            return None
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None


artifact_service = ArtifactService()
