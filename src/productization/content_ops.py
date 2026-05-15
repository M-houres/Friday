"""Content, knowledge, records, and result domain operations."""

from __future__ import annotations

import json
from typing import Any
import uuid

from sqlalchemy import text

from src.productization.base_service import _json_dumps


class ContentOpsMixin:
    async def create_template(
        self,
        name: str,
        content: str,
        *,
        template_id: str = "",
        category: str = "general",
        project_id: str = "",
        scope: str = "project",
        variables: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        template_id = template_id or uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO prompt_templates (id, name, category, project_id, scope, content, variables, metadata, created_at, updated_at)
                VALUES (:id, :name, :category, :project_id, :scope, :content, CAST(:variables AS JSONB), CAST(:metadata AS JSONB), NOW(), NOW())
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    project_id = EXCLUDED.project_id,
                    scope = EXCLUDED.scope,
                    content = EXCLUDED.content,
                    variables = EXCLUDED.variables,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "id": template_id,
                "name": name,
                "category": category,
                "project_id": project_id,
                "scope": scope,
                "content": content,
                "variables": json.dumps(variables or [], ensure_ascii=False),
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        await self.db.commit()
        return {"template_id": template_id, "name": name, "category": category, "project_id": project_id}

    async def list_templates(self, project_id: str = "", category: str = "") -> list[dict]:
        query = "SELECT * FROM prompt_templates WHERE 1=1"
        params: dict[str, Any] = {}
        if project_id:
            query += " AND project_id = :project_id"
            params["project_id"] = project_id
        if category:
            query += " AND category = :category"
            params["category"] = category
        query += " ORDER BY updated_at DESC"
        return await self._fetch_all(query, params)

    async def get_template(self, template_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            "SELECT * FROM prompt_templates WHERE id = :id",
            {"id": template_id},
        )

    async def delete_template(self, template_id: str) -> bool:
        result = await self.db.execute(text("DELETE FROM prompt_templates WHERE id = :id"), {"id": template_id})
        await self.db.commit()
        return bool(result.rowcount)

    async def create_knowledge_document(
        self,
        title: str,
        content: str,
        *,
        project_id: str = "",
        document_id: str = "",
        doc_type: str = "note",
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        document_id = document_id or uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO knowledge_documents (id, project_id, title, content, doc_type, tags, metadata, created_at, updated_at)
                VALUES (:id, :project_id, :title, :content, :doc_type, CAST(:tags AS JSONB), CAST(:metadata AS JSONB), NOW(), NOW())
                ON CONFLICT (id) DO UPDATE
                SET project_id = EXCLUDED.project_id,
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    doc_type = EXCLUDED.doc_type,
                    tags = EXCLUDED.tags,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW()
                """
            ),
            {
                "id": document_id,
                "project_id": project_id,
                "title": title,
                "content": content,
                "doc_type": doc_type,
                "tags": json.dumps(tags or [], ensure_ascii=False),
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        await self.db.commit()
        return {"document_id": document_id, "project_id": project_id, "title": title}

    async def list_knowledge_documents(self, project_id: str = "", doc_type: str = "", tag: str = "") -> list[dict]:
        query = "SELECT * FROM knowledge_documents WHERE 1=1"
        params: dict[str, Any] = {}
        if project_id:
            query += " AND project_id = :project_id"
            params["project_id"] = project_id
        if doc_type:
            query += " AND doc_type = :doc_type"
            params["doc_type"] = doc_type
        query += " ORDER BY updated_at DESC"
        documents = await self._fetch_all(query, params)
        if tag:
            tag_lower = tag.lower()
            documents = [
                doc for doc in documents
                if any(str(item).lower() == tag_lower for item in (doc.get("tags") or []))
            ]
        return documents

    async def get_knowledge_document(self, document_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            "SELECT * FROM knowledge_documents WHERE id = :id",
            {"id": document_id},
        )

    async def delete_knowledge_document(self, document_id: str) -> bool:
        result = await self.db.execute(text("DELETE FROM knowledge_documents WHERE id = :id"), {"id": document_id})
        await self.db.commit()
        return bool(result.rowcount)

    async def build_knowledge_context(
        self,
        project_id: str,
        query: str = "",
        limit: int = 5,
        doc_type: str = "",
        tag: str = "",
    ) -> dict:
        docs = await self.list_knowledge_documents(project_id, doc_type=doc_type, tag=tag)
        ranked = self.rank_knowledge_documents(docs, query=query, limit=limit)
        return {
            "project_id": project_id,
            "query": query,
            "doc_type": doc_type,
            "tag": tag,
            "matches": ranked,
        }

    async def create_product_record(
        self,
        project_id: str,
        record_type: str,
        title: str,
        payload: dict,
        *,
        record_id: str = "",
        user_id: str = "default",
        status: str = "draft",
    ) -> dict:
        record_id = record_id or uuid.uuid4().hex
        await self.db.execute(
            text(
                """
                INSERT INTO product_records (id, project_id, user_id, record_type, title, status, payload, created_at, updated_at)
                VALUES (:id, :project_id, :user_id, :record_type, :title, :status, CAST(:payload AS JSONB), NOW(), NOW())
                ON CONFLICT (id) DO UPDATE
                SET project_id = EXCLUDED.project_id,
                    user_id = EXCLUDED.user_id,
                    record_type = EXCLUDED.record_type,
                    title = EXCLUDED.title,
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload,
                    updated_at = NOW()
                """
            ),
            {
                "id": record_id,
                "project_id": project_id,
                "user_id": user_id,
                "record_type": record_type,
                "title": title,
                "status": status,
                "payload": _json_dumps(payload),
            },
        )
        await self.db.commit()
        return {"record_id": record_id, "project_id": project_id, "record_type": record_type, "status": status}

    async def get_product_record(self, record_id: str) -> dict | None:
        return await self._fetch_one_or_none(
            "SELECT * FROM product_records WHERE id = :id",
            {"id": record_id},
        )

    async def list_product_records(
        self,
        project_id: str = "",
        record_type: str = "",
        user_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM product_records WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            query += " AND project_id = :project_id"
            params["project_id"] = project_id
        if record_type:
            query += " AND record_type = :record_type"
            params["record_type"] = record_type
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        query += " ORDER BY updated_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    async def save_result_record(
        self,
        workflow_id: str,
        normalized_result: dict,
        *,
        project_id: str = "",
        page_id: str = "",
        user_id: str = "default",
    ) -> dict:
        await self.db.execute(
            text(
                """
                INSERT INTO result_records (id, workflow_id, project_id, page_id, user_id, summary, normalized_result, created_at, updated_at)
                VALUES (:id, :workflow_id, :project_id, :page_id, :user_id, :summary, CAST(:normalized_result AS JSONB), NOW(), NOW())
                ON CONFLICT (workflow_id) DO UPDATE
                SET project_id = EXCLUDED.project_id,
                    page_id = EXCLUDED.page_id,
                    user_id = EXCLUDED.user_id,
                    summary = EXCLUDED.summary,
                    normalized_result = EXCLUDED.normalized_result,
                    updated_at = NOW()
                """
            ),
            {
                "id": uuid.uuid4().hex,
                "workflow_id": workflow_id,
                "project_id": project_id,
                "page_id": page_id,
                "user_id": user_id,
                "summary": normalized_result.get("summary", ""),
                "normalized_result": _json_dumps(normalized_result),
            },
        )
        await self.db.commit()
        return {"workflow_id": workflow_id, "summary": normalized_result.get("summary", "")}

    async def get_result_record(self, workflow_id: str, user_id: str = "") -> dict | None:
        query = "SELECT * FROM result_records WHERE workflow_id = :workflow_id"
        params: dict[str, Any] = {"workflow_id": workflow_id}
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        return await self._fetch_one_or_none(query, params)

    async def list_result_records(
        self,
        project_id: str = "",
        page_id: str = "",
        user_id: str = "",
        workflow_id: str = "",
        limit: int = 100,
    ) -> list[dict]:
        query = "SELECT * FROM result_records WHERE 1=1"
        params: dict[str, Any] = {"limit": limit}
        if project_id:
            query += " AND project_id = :project_id"
            params["project_id"] = project_id
        if page_id:
            query += " AND page_id = :page_id"
            params["page_id"] = page_id
        if user_id:
            query += " AND user_id = :user_id"
            params["user_id"] = user_id
        if workflow_id:
            query += " AND workflow_id = :workflow_id"
            params["workflow_id"] = workflow_id
        query += " ORDER BY updated_at DESC LIMIT :limit"
        return await self._fetch_all(query, params)

    @staticmethod
    def render_template_content(content: str, variables: dict | None = None) -> str:
        rendered = content
        for key, value in (variables or {}).items():
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        return rendered

    @staticmethod
    def rank_knowledge_documents(documents: list[dict], query: str = "", limit: int = 5) -> list[dict]:
        query_terms = {term for term in query.lower().split() if term}
        if not query_terms:
            return [
                {
                    **doc,
                    "score": 0,
                    "snippet": ContentOpsMixin.extract_knowledge_snippet(str(doc.get("content", "")), query_terms),
                }
                for doc in documents[:limit]
            ]

        scored: list[tuple[int, dict]] = []
        for doc in documents:
            title = str(doc.get("title", "")).lower()
            content = str(doc.get("content", "")).lower()
            tags = " ".join(str(tag).lower() for tag in (doc.get("tags") or []))
            score = (
                sum(4 for term in query_terms if term in title)
                + sum(2 for term in query_terms if term in tags)
                + sum(1 for term in query_terms if term in content)
            )
            scored.append(
                (
                    score,
                    {
                        **doc,
                        "score": score,
                        "snippet": ContentOpsMixin.extract_knowledge_snippet(str(doc.get("content", "")), query_terms),
                    },
                )
            )
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc for score, doc in scored if score > 0][:limit]

    @staticmethod
    def extract_knowledge_snippet(content: str, query_terms: set[str], max_length: int = 180) -> str:
        text = content.strip()
        if not text:
            return ""
        if not query_terms:
            return text[:max_length]
        lowered = text.lower()
        first_index = min((lowered.find(term) for term in query_terms if term in lowered), default=-1)
        if first_index < 0:
            return text[:max_length]
        start = max(first_index - 30, 0)
        snippet = text[start:start + max_length]
        return snippet if start == 0 else f"...{snippet}"
