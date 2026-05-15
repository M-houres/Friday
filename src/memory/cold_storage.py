"""S3 冷存储适配器 —— 对话归档 / 调试日志 持久化到对象存储"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class S3ColdStorage:
    """S3 / MinIO 冷存储

    支持的实现:
    - boto3 (AWS S3 / MinIO / 兼容 S3 的服务)
    - 本地文件系统 (开发/单机部署)

    用法:
        storage = S3ColdStorage(endpoint_url="http://localhost:9000", bucket="friday-cold")
        await storage.connect()
        await storage.store("sessions/sess_abc.json", data)
    """

    def __init__(
        self,
        endpoint_url: str = "",
        access_key: str = "",
        secret_key: str = "",
        bucket: str = "friday-cold",
        region: str = "us-east-1",
        use_local_fs: bool = False,
        local_path: str = "/data/cold",
    ):
        self.endpoint_url = endpoint_url
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.region = region
        self.use_local_fs = use_local_fs
        self.local_path = local_path
        self._client = None

    async def connect(self):
        """建立连接 / 准备本地目录"""
        if self.use_local_fs:
            import os
            os.makedirs(self.local_path, exist_ok=True)
            logger.info(f"S3ColdStorage: using local FS at {self.local_path}")
            return

        try:
            import boto3
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url or None,
                aws_access_key_id=self.access_key or None,
                aws_secret_access_key=self.secret_key or None,
                region_name=self.region,
            )
            # 确保 bucket 存在
            try:
                self._client.head_bucket(Bucket=self.bucket)
            except Exception:
                if self.region == "us-east-1":
                    self._client.create_bucket(Bucket=self.bucket)
                else:
                    self._client.create_bucket(
                        Bucket=self.bucket,
                        CreateBucketConfiguration={"LocationConstraint": self.region},
                    )
            logger.info(f"S3ColdStorage: connected to {self.endpoint_url or 'AWS'}, bucket={self.bucket}")
        except ImportError:
            logger.warning("boto3 not installed, falling back to local FS cold storage")
            self.use_local_fs = True
            import os
            os.makedirs(self.local_path, exist_ok=True)
        except Exception as e:
            logger.error(f"S3ColdStorage connection failed: {e}")
            self.use_local_fs = True
            import os
            os.makedirs(self.local_path, exist_ok=True)

    async def store(self, key: str, data: dict | list | str, content_type: str = "application/json"):
        """存储对象"""
        if isinstance(data, (dict, list)):
            body = json.dumps(data, ensure_ascii=False, default=str)
        else:
            body = str(data)

        if self.use_local_fs:
            return await self._store_local(key, body)

        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType=content_type,
            )
            logger.debug(f"S3 stored: {key}")
        except Exception as e:
            logger.error(f"S3 store failed ({key}): {e}")
            return await self._store_local(key, body)

    async def retrieve(self, key: str) -> Optional[bytes]:
        """获取对象"""
        if self.use_local_fs:
            return await self._retrieve_local(key)

        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except Exception as e:
            logger.warning(f"S3 retrieve failed ({key}): {e}")
            return None

    async def retrieve_json(self, key: str) -> Optional[dict]:
        """获取 JSON 对象"""
        data = await self.retrieve(key)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    async def delete(self, key: str):
        """删除对象"""
        if self.use_local_fs:
            import os
            local_key = os.path.join(self.local_path, key)
            try:
                os.remove(local_key)
            except FileNotFoundError:
                pass
            return

        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.warning(f"S3 delete failed ({key}): {e}")

    async def list_keys(self, prefix: str = "", max_keys: int = 100) -> list[str]:
        """列出对象键"""
        if self.use_local_fs:
            import os
            import glob
            pattern = os.path.join(self.local_path, prefix + "*" if prefix else "*")
            paths = glob.glob(pattern)
            return [os.path.relpath(p, self.local_path) for p in paths[:max_keys]]

        try:
            response = self._client.list_objects_v2(
                Bucket=self.bucket, Prefix=prefix, MaxKeys=max_keys,
            )
            return [obj["Key"] for obj in response.get("Contents", [])]
        except Exception as e:
            logger.warning(f"S3 list failed: {e}")
            return []

    async def archive_session(self, session_id: str, messages: list[dict]):
        """归档一个 session 到冷存储"""
        now = datetime.now(timezone.utc)
        key = f"sessions/{now.strftime('%Y/%m/%d')}/{session_id}.json"
        data = {
            "session_id": session_id,
            "archived_at": now.isoformat(),
            "message_count": len(messages),
            "messages": messages,
        }
        await self.store(key, data)
        return key

    async def close(self):
        """关闭连接"""
        if self._client:
            self._client.close()
            self._client = None

    async def _store_local(self, key: str, data: str):
        import os
        full_path = os.path.join(self.local_path, key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(data)
        logger.debug(f"Local FS stored: {full_path}")

    async def _retrieve_local(self, key: str) -> Optional[bytes]:
        import os
        full_path = os.path.join(self.local_path, key)
        if not os.path.exists(full_path):
            return None
        with open(full_path, "rb") as f:
            return f.read()


cold_storage = S3ColdStorage()
