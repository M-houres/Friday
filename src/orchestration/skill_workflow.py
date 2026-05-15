"""Skill workflow executor —— 确定性执行 Skill 声明的步骤链。"""

import json
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.stream import friday_stream
from src.productization.result_protocol import normalize_result_payload
from src.tools.skill import skill_registry

logger = logging.getLogger(__name__)


class SkillWorkflowExecutor:
    """执行命中 Skill 的工作流，不再让通用 Agent 猜工具。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._row_ids: dict[str, str] = {}

    async def execute(
        self,
        workflow_id: str,
        task: str,
        skill_name: str,
        context: dict | None = None,
        emit_lifecycle: bool = True,
        emit_finish: bool = True,
        node_prefix: str = "",
    ) -> dict:
        skill_cls = skill_registry.get(skill_name)
        if skill_cls is None:
            raise ValueError(f"Skill not found: {skill_name}")

        skill = skill_cls()
        workflow = skill_cls.get_workflow()
        if not workflow:
            raise ValueError(f"Skill {skill_name} has no workflow definition")

        if emit_lifecycle:
            await friday_stream.start(message_id=workflow_id, workflow_id=workflow_id)

        results_by_node, state = await skill.execute_workflow(
            task=task,
            context={
                **(context or {}),
                "_workflow_id": workflow_id,
                "_user_id": (context or {}).get("_user_id", "default"),
            },
            step_callback=lambda event, payload: self._on_step_event(
                workflow_id,
                event,
                self._prefix_payload(payload, node_prefix, skill_name),
            ),
        )

        terminal_node = workflow[-1]
        terminal_id = terminal_node.get("id") or terminal_node.get("tool", "")
        terminal_output = results_by_node.get(terminal_id, {})
        final_payload = terminal_output.get("data", terminal_output) if isinstance(terminal_output, dict) else terminal_output

        content = ""
        if isinstance(final_payload, dict):
            content = str(final_payload.get("summary") or final_payload.get("content") or "")
            if not content:
                content = json.dumps(final_payload, ensure_ascii=False, default=str)
        else:
            content = str(final_payload)

        if emit_finish:
            await friday_stream.finish(
                {
                    "workflow_id": workflow_id,
                    "skill": skill_name,
                    "final_step": terminal_id,
                },
                workflow_id=workflow_id,
            )

        normalized = normalize_result_payload(
            final_payload if isinstance(final_payload, dict) else {"content": content},
            source=f"skill:{skill_name}",
        )

        return {
            "content": content,
            "skill": skill_name,
            "sandbox_id": workflow_id,
            "final_step": terminal_id,
            "final_output": terminal_output,
            "normalized_result": normalized,
            "node_results": results_by_node,
            "state": {
                node.get("id") or node.get("tool", ""): results_by_node.get(node.get("id") or node.get("tool", ""))
                for node in workflow
            },
        }

    @staticmethod
    def _prefix_payload(payload: dict, node_prefix: str, skill_name: str) -> dict:
        if not node_prefix:
            return payload

        node_id = payload["node_id"]
        node = dict(payload["node"])
        node["name"] = f"[{skill_name}] {node.get('name', node_id)}"

        return {
            **payload,
            "node_id": f"{node_prefix}:{node_id}",
            "node": node,
        }

    async def _on_step_event(self, workflow_id: str, event: str, payload: dict):
        node = payload["node"]
        node_id = payload["node_id"]
        row_id = self._row_ids.get(node_id)

        if event == "start":
            row_id = str(uuid.uuid4())
            self._row_ids[node_id] = row_id
            await self.db.execute(
                text("""
                    INSERT INTO workflow_nodes (id, workflow_id, node_id, task, dependencies, status, started_at)
                    VALUES (:id, :workflow_id, :node_id, :task, :dependencies, 'running', NOW())
                """),
                {
                    "id": row_id,
                    "workflow_id": workflow_id,
                    "node_id": node_id,
                    "task": node.get("name", node_id),
                    "dependencies": node.get("dependencies", []),
                },
            )
            await self.db.commit()
            await friday_stream.workflow_step_start(
                step_id=node_id,
                step_name=node.get("name", node_id),
                step_index=payload["step_index"],
                total_steps=payload["total_steps"],
                workflow_id=workflow_id,
            )
            return

        if row_id is None:
            return

        if event == "complete":
            output = payload.get("output", {})
            await self.db.execute(
                text("""
                    UPDATE workflow_nodes
                    SET status = 'completed',
                        result = :result,
                        completed_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": row_id,
                    "result": json.dumps(output, ensure_ascii=False, default=str),
                },
            )
            await self.db.commit()
            await friday_stream.workflow_step_complete(
                step_id=node_id,
                output=output if isinstance(output, dict) else {"value": output},
                workflow_id=workflow_id,
            )
            return

        if event == "error":
            error_message = payload.get("error", "Unknown skill step error")
            await self.db.execute(
                text("""
                    UPDATE workflow_nodes
                    SET status = 'failed',
                        error = :error,
                        completed_at = NOW()
                    WHERE id = :id
                """),
                {
                    "id": row_id,
                    "error": error_message[:500],
                },
            )
            await self.db.commit()
            await friday_stream.workflow_step_error(
                step_id=node_id,
                error=error_message,
                workflow_id=workflow_id,
            )
