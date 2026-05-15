"""Page scenario executor —— 在一个产品页面中顺序编排多个 Skill。"""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestration.skill_workflow import SkillWorkflowExecutor
from src.productization.result_protocol import normalize_result_payload
from src.projects.registry import project_registry

logger = logging.getLogger(__name__)


class PageScenarioExecutor:
    """执行 project/page 定义的多 Skill 场景。"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.skill_executor = SkillWorkflowExecutor(db)

    async def execute(
        self,
        workflow_id: str,
        project_id: str,
        page_id: str,
        task: str,
        user_id: str = "default",
        context: dict | None = None,
    ) -> dict:
        project = project_registry.get_project_manifest(project_id)
        if project is None:
            raise ValueError(f"Project not found: {project_id}")

        page = project_registry.get_page(project_id, page_id)
        if page is None:
            raise ValueError(f"Page not found: {project_id}/{page_id}")

        scenario = page.get("scenario", {})
        steps = scenario.get("steps", [])
        if not steps:
            raise ValueError(f"Page scenario has no steps: {project_id}/{page_id}")

        checkpoint = dict((context or {}).get("_scenario_checkpoint") or {})
        scenario_outputs: dict[str, dict] = deepcopy(checkpoint.get("scenario_outputs") or {})
        aggregate_state = dict(context or {})
        aggregate_state.update(deepcopy(checkpoint.get("scenario_state") or {}))
        aggregate_state.setdefault("task", task)
        aggregate_state.setdefault("_scenario_results", {})
        aggregate_state.setdefault("_scenario_errors", {})
        aggregate_state.setdefault("_scenario_approvals", {})

        index = int(checkpoint.get("next_step_index") or 0)
        pending_checkpoint: dict[str, Any] | None = None
        while index < len(steps):
            step = steps[index]
            if not self._should_run(step, aggregate_state):
                index += 1
                continue

            group = step.get("group", "")
            if group:
                grouped_steps = [step]
                lookahead = index + 1
                while lookahead < len(steps) and steps[lookahead].get("group", "") == group:
                    if self._should_run(steps[lookahead], aggregate_state):
                        grouped_steps.append(steps[lookahead])
                    lookahead += 1

                results = await asyncio.gather(
                    *[
                        self._execute_step(
                            workflow_id,
                            project_id,
                            page_id,
                            task,
                            user_id,
                            aggregate_state,
                            scenario_step,
                            emit_lifecycle=index == 0 and inner_index == 0,
                        )
                        for inner_index, scenario_step in enumerate(grouped_steps)
                    ]
                )
                for result_step_id, result_item, final_output in results:
                    scenario_outputs[result_step_id] = result_item
                    if self._is_pending_approval(result_item):
                        pending_checkpoint = self._build_checkpoint(aggregate_state, scenario_outputs, index)
                        break
                    self._write_step_state(aggregate_state, result_step_id, result_item, final_output)
                if pending_checkpoint is not None:
                    break
                index = lookahead
                continue

            step_id, result_item, final_output = await self._execute_step(
                workflow_id,
                project_id,
                page_id,
                task,
                user_id,
                aggregate_state,
                step,
                emit_lifecycle=index == 0,
            )
            scenario_outputs[step_id] = result_item
            if self._is_pending_approval(result_item):
                pending_checkpoint = self._build_checkpoint(aggregate_state, scenario_outputs, index)
                break
            self._write_step_state(aggregate_state, step_id, result_item, final_output)
            index += 1

        final_output = self._merge_results(page, scenario_outputs)
        from src.api.stream import friday_stream

        await friday_stream.finish(
            {
                "workflow_id": workflow_id,
                "project_id": project_id,
                "page_id": page_id,
                "steps": list(scenario_outputs.keys()),
            },
            workflow_id=workflow_id,
        )

        return {
            "content": final_output.get("summary", ""),
            "project_id": project_id,
            "page_id": page_id,
            "page_name": page.get("name", page_id),
            "scenario_results": scenario_outputs,
            "final_output": {"success": True, "data": final_output},
            "normalized_result": normalize_result_payload(final_output, source=f"page:{project_id}/{page_id}"),
            "state": aggregate_state,
            "checkpoint": pending_checkpoint,
        }

    @staticmethod
    def _render_task(template: str, task: str, state: dict) -> str:
        if not template:
            return task
        rendered = template.replace("{{task}}", task)
        scenario_results = state.get("_scenario_results", {})
        for step_id, result in scenario_results.items():
            payload = result.get("data", result) if isinstance(result, dict) else result
            rendered = rendered.replace(f"{{{{result:{step_id}}}}}", json.dumps(payload, ensure_ascii=False, default=str))
        return rendered

    @classmethod
    def _should_run(cls, step: dict, state: dict) -> bool:
        rule = step.get("run_if", "")
        if not rule:
            return True
        if "==" in rule:
            left, right = rule.split("==", 1)
            value = cls._resolve_value(left.strip(), state)
            return str(value) == right.strip()
        if "!=" in rule:
            left, right = rule.split("!=", 1)
            value = cls._resolve_value(left.strip(), state)
            return str(value) != right.strip()
        if "=" in rule:
            left, right = rule.split("=", 1)
            value = cls._resolve_value(left.strip(), state)
            return str(value) == right.strip()
        if rule.startswith("!"):
            return not bool(cls._resolve_value(rule[1:].strip(), state))
        return bool(cls._resolve_value(rule.strip(), state))

    async def _execute_step(
        self,
        workflow_id: str,
        project_id: str,
        page_id: str,
        task: str,
        user_id: str,
        aggregate_state: dict,
        step: dict,
        *,
        emit_lifecycle: bool,
    ) -> tuple[str, dict, dict]:
        skill_name = step.get("skill", "")
        if not skill_name:
            raise ValueError(f"Scenario step missing skill: {step}")

        step_id = step.get("id", skill_name)
        step_task = self._render_task(step.get("task_template", ""), task, aggregate_state)
        step_context = self._build_step_context(aggregate_state, step, task)

        if step.get("approval_required"):
            approval_state = aggregate_state.get("_approvals", {}).get(step_id)
            if not approval_state:
                approval_state = aggregate_state["_scenario_approvals"].get(step_id)
            approved = bool(
                approval_state is True
                or (isinstance(approval_state, dict) and approval_state.get("approved"))
            )
            if not approved:
                aggregate_state["_scenario_approvals"][step_id] = {
                    "required": True,
                    "status": "pending",
                    "note": step.get("approval_note", ""),
                }
                result_item = {
                    "skill": skill_name,
                    "name": step.get("name", skill_name),
                    "task": step_task or task,
                    "status": "approval_required",
                    "approval_required": True,
                    "approval_note": step.get("approval_note", ""),
                    "result": None,
                }
                final_output = {
                    "success": False,
                    "status": "approval_required",
                    "summary": step.get("approval_note", f"{step.get('name', step_id)} 等待人工确认。"),
                }
                aggregate_state["_scenario_errors"][step_id] = final_output["summary"]
                return step_id, result_item, final_output
            aggregate_state["_scenario_approvals"][step_id] = {
                "required": True,
                "status": "approved",
                "note": step.get("approval_note", ""),
            }

        try:
            execution = await self.skill_executor.execute(
                workflow_id=workflow_id,
                task=step_task or task,
                skill_name=skill_name,
                context={
                    **step_context,
                    "_user_id": user_id,
                    "_scenario_step_id": step_id,
                    "_scenario_step_name": step.get("name", skill_name),
                    "_scenario_project_id": project_id,
                    "_scenario_page_id": page_id,
                },
                emit_lifecycle=emit_lifecycle,
                emit_finish=False,
                node_prefix=f"{page_id}:{step_id}",
            )
            final_output = self._resolve_step_output(execution.get("final_output", {}))
            result_item = {
                "skill": skill_name,
                "name": step.get("name", skill_name),
                "task": step_task or task,
                "status": "completed",
                "result": execution,
            }
            self._apply_output_mapping(aggregate_state, step, final_output)
            return step_id, result_item, final_output
        except Exception as exc:
            logger.warning("Scenario step failed %s/%s/%s: %s", project_id, page_id, step_id, exc)
            fallback_template = step.get("fallback_task_template", "")
            if fallback_template:
                fallback_task = self._render_task(fallback_template, task, aggregate_state)
                fallback_payload = {
                    "success": True,
                    "summary": fallback_task or f"{step.get('name', step_id)} 已回退处理。",
                    "fallback": True,
                    "error": str(exc),
                }
                self._apply_output_mapping(aggregate_state, step, fallback_payload)
                return (
                    step_id,
                    {
                        "skill": skill_name,
                        "name": step.get("name", skill_name),
                        "task": step_task or task,
                        "status": "fallback",
                        "error": str(exc),
                        "result": {"final_output": fallback_payload},
                    },
                    fallback_payload,
                )

            aggregate_state["_scenario_errors"][step_id] = str(exc)
            if step.get("continue_on_error"):
                error_payload = {
                    "success": False,
                    "summary": f"{step.get('name', step_id)} 执行失败：{exc}",
                    "error": str(exc),
                }
                self._apply_output_mapping(aggregate_state, step, error_payload)
                return (
                    step_id,
                    {
                        "skill": skill_name,
                        "name": step.get("name", skill_name),
                        "task": step_task or task,
                        "status": "failed",
                        "error": str(exc),
                        "result": {"final_output": error_payload},
                    },
                    error_payload,
                )
            raise

    @staticmethod
    def _resolve_step_output(final_output: Any) -> dict:
        if isinstance(final_output, dict) and "data" in final_output and isinstance(final_output["data"], dict):
            return dict(final_output["data"])
        if isinstance(final_output, dict):
            return dict(final_output)
        return {"content": str(final_output)}

    def _build_step_context(self, aggregate_state: dict, step: dict, task: str) -> dict:
        context = deepcopy(aggregate_state)
        input_mappings = step.get("inputs", {}) or {}
        if not input_mappings:
            return context
        mapped: dict[str, Any] = {}
        for target_key, source_expr in input_mappings.items():
            mapped[target_key] = self._resolve_value(source_expr, aggregate_state, default=task)
        context["_step_inputs"] = mapped
        context.update(mapped)
        return context

    @classmethod
    def _resolve_value(cls, expr: str, state: dict, default: Any = "") -> Any:
        if not expr:
            return default
        if expr == "task":
            return state.get("task", default)
        if expr.startswith("literal:"):
            return expr.split("literal:", 1)[1]
        current: Any = state
        for part in expr.split("."):
            key = part.strip()
            if not key:
                continue
            if isinstance(current, dict) and key in current:
                current = current[key]
                continue
            return default
        return current

    def _apply_output_mapping(self, state: dict, step: dict, output: dict):
        for target_key, source_expr in (step.get("outputs", {}) or {}).items():
            state[target_key] = deepcopy(self._resolve_value(source_expr, output))

    @staticmethod
    def _is_pending_approval(result_item: dict) -> bool:
        return result_item.get("status") == "approval_required"

    @staticmethod
    def _build_checkpoint(aggregate_state: dict, scenario_outputs: dict[str, dict], next_step_index: int) -> dict:
        completed_outputs = {
            step_id: deepcopy(item)
            for step_id, item in scenario_outputs.items()
            if item.get("status") != "approval_required"
        }
        return {
            "scenario_state": deepcopy(aggregate_state),
            "scenario_outputs": completed_outputs,
            "next_step_index": next_step_index,
        }

    @staticmethod
    def _write_step_state(aggregate_state: dict, step_id: str, result_item: dict, final_output: dict):
        aggregate_state[result_item["skill"]] = final_output
        aggregate_state[step_id] = final_output
        aggregate_state["_scenario_results"][step_id] = final_output

    @staticmethod
    def _merge_results(page: dict, scenario_outputs: dict[str, dict]) -> dict:
        summary_parts: list[str] = []
        downloads: list[dict] = []
        approvals: list[dict] = []
        errors: list[dict] = []
        merged: dict[str, object] = {
            "page_name": page.get("name", ""),
            "page_route": page.get("route", ""),
            "steps": [],
            "downloads": downloads,
            "approvals": approvals,
            "errors": errors,
        }

        for step_id, item in scenario_outputs.items():
            result = item.get("result") or {}
            final_output = result.get("final_output", {}) if isinstance(result, dict) else {}
            payload = final_output.get("data", final_output) if isinstance(final_output, dict) else final_output
            merged["steps"].append(
                {
                    "id": step_id,
                    "name": item.get("name", step_id),
                    "skill": item.get("skill", ""),
                    "status": item.get("status", "completed"),
                    "output": payload,
                }
            )
            if item.get("status") == "approval_required":
                approvals.append(
                    {
                        "id": step_id,
                        "name": item.get("name", step_id),
                        "note": item.get("approval_note", ""),
                    }
                )
            if item.get("error"):
                errors.append(
                    {
                        "id": step_id,
                        "name": item.get("name", step_id),
                        "error": item.get("error", ""),
                    }
                )
            if isinstance(payload, dict):
                summary = payload.get("summary", "")
                if summary:
                    summary_parts.append(summary)
                if payload.get("download_url"):
                    downloads.append(
                        {
                            "skill": item.get("skill", ""),
                            "filename": payload.get("filename", ""),
                            "download_url": payload.get("download_url", ""),
                        }
                    )

        merged["summary"] = "\n\n".join(summary_parts) if summary_parts else f"{page.get('name', '页面场景')}执行完成。"
        if approvals:
            merged["status"] = "approval_required"
        elif errors:
            merged["status"] = "partial"
        else:
            merged["status"] = "completed"
        if downloads:
            merged["download_url"] = downloads[0]["download_url"]
        return merged
