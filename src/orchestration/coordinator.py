"""Coordinator —— 编排总控，串联 Plan → Dispatch → Aggregate"""

import asyncio
import json
import logging
import uuid
from enum import Enum

from sqlalchemy.ext.asyncio import AsyncSession

from src.orchestration.dag import DAG, DAGNode, NodeStatus
from src.orchestration.planner import Planner
from src.orchestration.dispatcher import Dispatcher
from src.orchestration.aggregator import Aggregator
from src.orchestration.coordinator_helpers import extract_approvals, normalize_final_result
from src.orchestration.coordinator_store import CoordinatorStore
from src.orchestration.page_scenario import PageScenarioExecutor
from src.orchestration.skill_workflow import SkillWorkflowExecutor
from src.productization.managed_config import managed_config_store
from src.productization.domain_services import BillingOpsService, ContentOpsService, SupportOpsService
from src.tools.isolated_sandbox import sandbox_pool
from src.tools.harness import ToolHarness

logger = logging.getLogger(__name__)


class DegradationLevel(Enum):
    FULL = 0           # 完美完成
    MODEL_FALLBACK = 1  # 模型降级
    TOOL_FALLBACK = 2   # 工具降级
    PARTIAL = 3         # 部分结果
    CACHED = 4          # 缓存结果
    FAIL = 5            # 失败


class Coordinator:
    """编排总控"""

    def __init__(self, db: AsyncSession, model: str | None = None):
        self.db = db
        self.store = CoordinatorStore(db)
        self.planner = Planner(model)
        self.dispatcher = Dispatcher(max_parallel=20)
        self.aggregator = Aggregator(model)
        self._coordinator_id = f"coord-{uuid.uuid4().hex[:8]}"
        self._requested_model = model or ""
        self._active_model = model or ""
        self._active_fast_model = ""

    async def execute(
        self,
        task: str,
        user_id: str = "default",
        mode: str = "auto",
        context: dict | None = None,
        project_id: str | None = None,
        page_id: str | None = None,
        workflow_id: str | None = None,
    ) -> dict:
        """执行完整编排流程"""
        context = dict(context or {})
        billing_service = BillingOpsService(self.db)
        content_service = ContentOpsService(self.db)
        approval_service = SupportOpsService(self.db)
        billing = dict(context.get("_billing") or {})
        billed_now = False
        billed_credits = int(billing.get("credits_cost") or 0)

        if billing.get("required") and billed_credits > 0 and not billing.get("charged"):
            await billing_service.consume_user_credits(
                user_id,
                billed_credits,
                reason=f"{billing.get('reason') or 'workflow'}:{project_id or ''}:{page_id or ''}",
            )
            billing["charged"] = True
            context["_billing"] = billing
            billed_now = True

        if project_id:
            try:
                knowledge_context = await content_service.build_knowledge_context(project_id, query=task)
                context["_knowledge_context"] = knowledge_context
            except Exception as exc:
                logger.debug("Knowledge context skipped for %s: %s", project_id, exc)

        wf_id = str(workflow_id or context.get("_approval_parent_workflow_id") or uuid.uuid4())
        sandbox = await sandbox_pool.acquire(workflow_id=wf_id)
        self._active_model = managed_config_store.resolve_model(
            task,
            project_id=project_id or "",
            page_id=page_id or "",
            preferred=self._requested_model,
        )
        self._active_fast_model = managed_config_store.resolve_fast_model(
            project_id=project_id or "",
            page_id=page_id or "",
        )
        self.planner.model = self._active_model
        self.aggregator.model = self._active_model
        await self.store.initialize_workflow(
            wf_id,
            user_id=user_id,
            task=task,
            coordinator_id=self._coordinator_id,
            resume_existing=bool(workflow_id or context.get("_approval_parent_workflow_id")),
        )

        try:
            # 2. 页面场景优先
            if project_id and page_id:
                await self.store.update_workflow_status(wf_id, "executing")
                scenario_executor = PageScenarioExecutor(self.db)
                final = await scenario_executor.execute(
                    workflow_id=wf_id,
                    project_id=project_id,
                    page_id=page_id,
                    task=task,
                    user_id=user_id,
                    context=context,
                )
                results = final.get("scenario_results", {})
                failed = []
                degradation = DegradationLevel.FULL
                dag = DAG()
            else:
                # 2. 计划阶段
                await self.store.update_workflow_status(wf_id, "planning")
                plan = await self.planner.plan(task, context)
                dag = DAG.from_plan(plan)

                # 存储计划
                await self.store.save_plan(wf_id, dag)

                # 3. 执行阶段
                await self.store.update_workflow_status(wf_id, "executing")

                if plan.get("_execution_mode") == "skill_pipeline" and plan.get("_skill"):
                    skill_executor = SkillWorkflowExecutor(self.db)
                    final = await skill_executor.execute(
                        workflow_id=wf_id,
                        task=task,
                        skill_name=plan["_skill"],
                        context={
                            **(context or {}),
                            "_user_id": user_id,
                        },
                    )
                    results = final.get("node_results", {})
                    failed = []
                    degradation = DegradationLevel.FULL
                else:
                    results = await self.dispatcher.dispatch(
                        dag,
                        execute_fn=await self._create_node_executor(
                            wf_id,
                            project_id=project_id or "",
                            page_id=page_id or "",
                        ),
                        on_progress=lambda n: self._on_node_complete(wf_id, n),
                    )

                    # 4. 聚合阶段
                    await self.store.update_workflow_status(wf_id, "aggregating")
                    failed = [n_id for n_id, n in dag.nodes.items() if n.status == NodeStatus.FAILED]
                    degradation = self._calculate_degradation(dag)

                    final = await self.aggregator.aggregate(
                        task=task,
                        results=results,
                        failed_nodes=failed,
                        degradation_level=degradation.value,
                    )

            # 5. 完成
            normalized = normalize_final_result(
                final,
                project_id=project_id or "",
                page_id=page_id or "",
            )
            if billing.get("required") and billed_credits > 0:
                normalized["billing"] = {
                    "required": True,
                    "credits_cost": billed_credits,
                    "reason": billing.get("reason") or "",
                    "charged": True,
                    "refunded": False,
                }
            workflow_status = "awaiting_approval" if normalized.get("status") == "approval_required" else "completed"
            await self.store.complete_workflow(
                wf_id,
                status=workflow_status,
                result=final,
                degradation_level=degradation.value,
            )

            await content_service.save_result_record(
                workflow_id=wf_id,
                normalized_result=normalized,
                project_id=project_id or "",
                page_id=page_id or "",
                user_id=user_id,
            )
            approvals = await self._create_approval_requests(
                approval_service,
                workflow_id=wf_id,
                user_id=user_id,
                task=task,
                mode=mode,
                context=context,
                project_id=project_id or "",
                page_id=page_id or "",
                final=final,
            )

            return {
                "workflow_id": wf_id,
                "sandbox_id": sandbox.sandbox_id,
                "status": workflow_status,
                "result": final,
                "normalized_result": normalized,
                "dags": dag.to_dict(),
                "degradation_level": degradation.value,
                "failed_nodes": failed,
                "approvals": approvals,
            }

        except Exception as e:
            logger.error(f"Workflow {wf_id} failed: {e}")
            if billed_now and billed_credits > 0:
                try:
                    await billing_service.refund_user_credits(
                        user_id,
                        billed_credits,
                        reason=f"workflow_failed:{wf_id}",
                    )
                except Exception as refund_err:
                    logger.error("Failed to refund credits for workflow %s: %s", wf_id, refund_err)
            try:
                await self.db.rollback()
            except Exception:
                pass
            try:
                await self.store.fail_workflow(wf_id, str(e)[:500])
            except Exception as db_err:
                logger.error(f"Failed to record workflow error: {db_err}")
            raise
        finally:
            await sandbox_pool.release(workflow_id=wf_id)

    async def _create_node_executor(self, wf_id: str, project_id: str = "", page_id: str = ""):
        """创建节点执行函数 —— 启动真正的 Agent 实例 (可选 JIT 加速)"""
        from src.config import settings

        async def execute_node(node: DAGNode) -> dict:
            node_db_id = await self.store.start_node(wf_id, node)

            # === JIT 加速路径 (可选) ===
            if settings.jit_enabled:
                jit_result = await self._try_jit_execute(node)
                if jit_result is not None:
                    return jit_result

            from src.core.agent_runtime import AgentRuntime

            system_prompt, model, tools, tool_handlers = await self._resolve_agent_config(
                node,
                project_id=project_id,
                page_id=page_id,
            )

            # === 投机执行路径 (可选) ===
            if settings.speculative_enabled and tools:
                result = await self._try_speculative_execute(node, system_prompt, model, tools, tool_handlers)
                if result is not None:
                    await self.store.complete_node(node_db_id, result, result.get("_model", model))
                    return result

            agent = AgentRuntime(
                name=node.node_id,
                system_prompt=system_prompt,
                model=model,
                tools=tools,
                tool_handlers=tool_handlers,
            )

            result = await agent.run(node.task)

            node_content = result.content if result.success else f"Error: {result.error}"
            node_tokens = result.tokens_used

            await self.store.complete_node(
                node_db_id,
                {
                    "content": node_content,
                    "agent_id": agent.id,
                    "steps": len(result.steps),
                    "degradation": result.degradation_level,
                    "_model": model,
                    "_tokens": node_tokens,
                },
                model,
            )

            return {"content": node_content, "agent": agent.id, "steps": len(result.steps)}

        return execute_node

    async def _try_jit_execute(self, node: DAGNode) -> dict | None:
        """尝试用 JIT 子图执行，跳过 LLM"""
        try:
            from src.orchestration.jit import jit_compiler
            state = {"task": node.task, "node_id": node.node_id}
            match = jit_compiler.find_match(state)
            if match is None:
                return None

            logger.info(f"JIT match: {match.name} for node {node.node_id}")

            async def tool_exec(name: str, args: dict) -> dict:
                return await ToolHarness(guardrail_name="jit").execute(name, args)

            results = await match.execute(tool_exec)
            jit_compiler.record_success(match)

            content = json.dumps(results, ensure_ascii=False, default=str)
            return {"content": content, "agent": "jit", "steps": len(results), "_model": "jit"}
        except Exception as e:
            logger.debug(f"JIT execution skipped: {e}")
            return None

    async def _try_speculative_execute(
        self, node: DAGNode, system_prompt: str, model: str,
        tools: list[dict], tool_handlers: dict
    ) -> dict | None:
        """尝试投机执行 —— 快慢模型赛跑"""
        try:
            from src.core.interleaved import SpeculativeExecutor
            from src.models.base import Message

            messages = [Message(role="system", content=system_prompt),
                        Message(role="user", content=node.task)]

            def validator(result: dict) -> bool:
                content = result.get("content", "")
                return bool(content) and len(content) > 10

            executor = SpeculativeExecutor(fast_models=[model, self._active_fast_model or settings.default_fast_model])
            result = await executor.speculative_generate(messages, validator)
            return {"content": result["content"], "agent": "spec", "steps": 1,
                    "_model": result["model"], "_tokens": result.get("tokens", 0)}
        except Exception as e:
            logger.debug(f"Speculative execution skipped: {e}")
            return None

    async def _resolve_agent_config(
        self,
        node: DAGNode,
        project_id: str = "",
        page_id: str = "",
    ) -> tuple[str, str, list[dict], dict]:
        """为节点解析 Agent 配置 —— 优先级: Skill > 预注册Agent > 默认"""
        system_prompt = "你是任务执行专家。请完成以下子任务，输出简洁完整的结果。"
        model = managed_config_store.resolve_model(
            node.task,
            project_id=project_id,
            page_id=page_id,
            preferred=self._requested_model,
        )
        tools = []
        tool_handlers = {}

        # 1. 尝试匹配 Skill
        if hasattr(node, "skill_name") and node.skill_name:
            from src.tools.skill import skill_registry
            skill_cls = skill_registry.get(node.skill_name)
            if skill_cls:
                skill_instance = skill_cls()
                system_prompt = skill_instance.description or system_prompt
                tools = skill_cls.get_tool_schemas()
                for t in skill_cls.get_tools():
                    def make_skill_handler(tn, si):
                        async def handler(**kwargs):
                            return await si.execute_tool(tn, **kwargs)
                        return handler
                    tool_handlers[t.name] = make_skill_handler(t.name, skill_instance)

        # 2. 尝试匹配预注册 Agent
        if not tools and hasattr(node, "agent_id") and node.agent_id:
            from sqlalchemy import text
            row = await self.db.execute(
                text("SELECT system_prompt, model, tools FROM agent_definitions WHERE id = :id"),
                {"id": node.agent_id},
            )
            agent_def = row.fetchone()
            if agent_def:
                agent_def = dict(agent_def._mapping)
                system_prompt = agent_def.get("system_prompt", system_prompt)
                model = agent_def.get("model", model)
                agent_tools = agent_def.get("tools", [])
                if agent_tools:
                    from src.tools.registry import tool_registry
                    tools = [
                        tool_registry.get_definition(t).to_openai_schema()
                        for t in agent_tools
                        if tool_registry.get_definition(t)
                    ]
                    for t in agent_tools:
                        handler = tool_registry.get_handler(t)
                        if handler:
                            tool_handlers[t] = handler

        # 3. 默认使用全局工具
        if not tools:
            from src.tools.registry import tool_registry
            tools = tool_registry.get_schemas()
            for name in tool_registry.list_tools():
                handler = tool_registry.get_handler(name)
                if handler:
                    tool_handlers[name] = handler

        return system_prompt, model, tools, tool_handlers

    async def _on_node_complete(self, wf_id: str, node: DAGNode):
        """节点完成回调"""
        logger.info(f"Workflow {wf_id}: node {node.node_id} → {node.status.value}")

    def _calculate_degradation(self, dag: DAG) -> DegradationLevel:
        """计算降级等级"""
        nodes_list = list(dag.nodes.values())
        has_failed = any(n.status == NodeStatus.FAILED for n in nodes_list)
        has_skipped = any(n.status == NodeStatus.SKIPPED for n in nodes_list)
        has_completed = any(n.status == NodeStatus.COMPLETED for n in nodes_list)

        # 全部失败 → FAIL
        if not has_completed and not has_skipped:
            return DegradationLevel.FAIL

        # 关键节点失败 → PARTIAL
        if has_failed and any(
            n.is_critical and n.status == NodeStatus.FAILED
            for n in nodes_list
        ):
            return DegradationLevel.PARTIAL

        # 有失败的但非关键 → TOOL_FALLBACK
        if has_failed:
            return DegradationLevel.TOOL_FALLBACK

        # 有跳过的 → CACHED (跳过多半因为缓存命中)
        if has_skipped:
            return DegradationLevel.CACHED

        # 检查是否用了回退模型
        for n in nodes_list:
            if n.result and n.result.get("content", ""):
                agent_id = n.result.get("agent", "")
                if agent_id == "jit" or agent_id == "spec":
                    return DegradationLevel.CACHED

        return DegradationLevel.FULL

    async def _create_approval_requests(
        self,
        service: SupportOpsService,
        *,
        workflow_id: str,
        user_id: str,
        task: str,
        mode: str,
        context: dict,
        project_id: str,
        page_id: str,
        final: dict,
    ) -> list[dict]:
        approvals = extract_approvals(final)
        checkpoint = final.get("checkpoint") if isinstance(final, dict) else None
        if not approvals:
            return []

        created: list[dict] = []
        for approval in approvals:
            detail = {
                "task": task,
                "mode": mode,
                "context": context,
                "project_id": project_id,
                "page_id": page_id,
                "step_id": approval.get("id", ""),
                "step_name": approval.get("name", ""),
                "note": approval.get("note", ""),
            }
            created_item = await service.create_approval_request(
                    workflow_id,
                    approval.get("id", ""),
                    approval.get("name", approval.get("id", "Approval")),
                    project_id=project_id,
                    page_id=page_id,
                    requester_user_id=user_id,
                    detail=detail,
                )
            if checkpoint:
                attached = await service.attach_approval_checkpoint(
                    created_item["approval_id"],
                    scenario_state=checkpoint.get("scenario_state") or {},
                    scenario_outputs=checkpoint.get("scenario_outputs") or {},
                    next_step_index=int(checkpoint.get("next_step_index") or 0),
                )
                if attached is not None:
                    created_item = attached
            created.append(created_item)
        return created
