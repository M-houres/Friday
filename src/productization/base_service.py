"""Shared base helpers for productization data services."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

UNSET = object()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _datetime_from_timestamp(value: float | int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc)


class BaseProductService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _is_missing_table_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "relation" in message and "does not exist" in message

    @staticmethod
    def _is_db_unavailable_error(exc: Exception) -> bool:
        message = str(exc).lower()
        keywords = (
            "connection refused",
            "could not connect",
            "connection is closed",
            "connection not open",
            "failed to establish",
            "cannot connect",
            "remote computer refused",
            "远程计算机拒绝网络连接",
        )
        return any(keyword in message for keyword in keywords)

    async def _execute_or_empty(self, query: Any, params: dict | None = None):
        statement = text(query) if isinstance(query, str) else query
        try:
            return await self.db.execute(statement, params or {})
        except (ProgrammingError, DBAPIError, OperationalError, ConnectionError, OSError) as exc:
            if self._is_missing_table_error(exc) or self._is_db_unavailable_error(exc):
                return None
            raise

    async def _fetch_one_or_none(self, query: Any, params: dict | None = None) -> dict | None:
        rows = await self._execute_or_empty(query, params)
        if rows is None:
            return None
        record = rows.fetchone()
        return dict(record._mapping) if record else None

    async def _fetch_all(self, query: Any, params: dict | None = None) -> list[dict]:
        rows = await self._execute_or_empty(query, params)
        if rows is None:
            return []
        return [dict(row._mapping) for row in rows.fetchall()]

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()
