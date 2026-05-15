"""运营后台 / 产品化相关 API 路由。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.auth import get_request_roles, get_request_user_id, require_roles
from src.api.schemas import ReviewApprovalRequest
from src.config import settings
from src.db import get_db
from src.productization.async_jobs import async_job_manager
from src.productization.domain_services import (
    AuditOpsService,
    BillingOpsService,
    ContentOpsService,
    GrowthOpsService,
    SupportOpsService,
    UserOpsService,
    WorkflowOpsService,
)
from src.productization.managed_config import managed_config_store
from src.productization.project_config_store import project_config_store

router = APIRouter()

OPS_VIEW_ROLES = {"admin", "operator"}
OPS_WRITE_ROLES = {"admin", "operator", "builder"}


def _is_dev_open_mode() -> bool:
    return settings.auth_mode == "none" and settings.environment != "prod"


def _has_any_role(request: Request, allowed_roles: set[str]) -> bool:
    return bool(set(get_request_roles(request)) & allowed_roles)


def _resolve_user_scope(request: Request, requested_user_id: str = "") -> str:
    if _is_dev_open_mode():
        return requested_user_id
    viewer_user_id = get_request_user_id(request)
    if _has_any_role(request, OPS_VIEW_ROLES):
        return requested_user_id
    if requested_user_id and requested_user_id != viewer_user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return requested_user_id or viewer_user_id


def _assert_owner_or_ops(request: Request, owner_user_id: str = ""):
    if _is_dev_open_mode() or not owner_user_id:
        return
    viewer_user_id = get_request_user_id(request)
    if owner_user_id == viewer_user_id or _has_any_role(request, OPS_VIEW_ROLES):
        return
    raise HTTPException(status_code=403, detail="Forbidden")


def _serialize_async_job(job: dict) -> dict:
    return {
        "job_id": str(job.get("job_id") or job.get("id") or ""),
        "job_type": job.get("job_type", ""),
        "status": job.get("status", "queued"),
        "priority": job.get("priority", 5),
        "payload": job.get("payload") or {},
        "result": job.get("result"),
        "error": job.get("error") or "",
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
    }


class UserUpsertRequest(BaseModel):
    user_id: str
    name: str = ""
    email: str = ""
    roles: list[str] = []
    status: str = "active"
    metadata: dict | None = None


class TemplateRequest(BaseModel):
    template_id: str = ""
    name: str
    content: str
    category: str = "general"
    project_id: str = ""
    scope: str = "project"
    variables: list[str] = Field(default_factory=list)
    metadata: dict | None = None


class KnowledgeDocumentRequest(BaseModel):
    document_id: str = ""
    project_id: str = ""
    title: str
    content: str
    doc_type: str = "note"
    tags: list[str] = Field(default_factory=list)
    metadata: dict | None = None


class ProductRecordRequest(BaseModel):
    record_id: str = ""
    project_id: str
    record_type: str
    title: str
    status: str = "draft"
    payload: dict = Field(default_factory=dict)


class BillingPlanRequest(BaseModel):
    plan_id: str = ""
    name: str
    plan_type: str = "subscription"
    price_cents: int = 0
    currency: str = "CNY"
    interval: str = "month"
    credits: int = 0
    features: list[str] = Field(default_factory=list)
    status: str = "active"
    metadata: dict | None = None


class PaymentOrderRequest(BaseModel):
    order_id: str = ""
    user_id: str
    plan_id: str = ""
    order_type: str = "subscription"
    amount_cents: int = 0
    currency: str = "CNY"
    status: str = "pending"
    provider: str = ""
    provider_order_id: str = ""
    credits_delta: int = 0
    detail: dict | None = None
    paid_at: float | int | None = None


class UserEntitlementRequest(BaseModel):
    user_id: str
    active_plan_id: str = ""
    subscription_status: str = "inactive"
    credits_balance: int = 0
    credits_granted_total: int = 0
    credits_used_total: int = 0
    expires_at: float | int | None = None
    metadata: dict | None = None


class UserOperationRequest(BaseModel):
    action: str
    note: str = ""
    credits_delta: int = 0


class ProjectManifestRequest(BaseModel):
    project_id: str
    name: str = ""
    description: str = ""
    home_route: str = ""
    skills: list[str] = Field(default_factory=list)
    pages: list[dict] | None = None


class ProjectPageRequest(BaseModel):
    page_id: str
    name: str = ""
    route: str = ""
    page: str = ""
    skills: list[str] = Field(default_factory=list)
    description: str = ""
    nav_label: str = ""
    icon: str = ""
    visibility: str = "public"
    is_home: bool = False
    billing: dict | None = None
    scenario: dict | None = None


class RefundOrderRequest(BaseModel):
    reason: str = ""


class RefundWorkflowRequest(BaseModel):
    reason: str = ""


class GrowthCouponRequest(BaseModel):
    code: str
    name: str
    credits_bonus: int = 0
    status: str = "active"
    max_redemptions: int = 0
    starts_at: float | int | None = None
    ends_at: float | int | None = None
    metadata: dict | None = None


class RedeemCouponRequest(BaseModel):
    code: str
    user_id: str
    metadata: dict | None = None


class TrialGrantRequest(BaseModel):
    user_id: str
    credits_amount: int = 0
    reason: str = ""
    metadata: dict | None = None


class SupportTicketRequest(BaseModel):
    ticket_id: str = ""
    user_id: str
    ticket_type: str = "general"
    title: str
    detail: dict | None = None
    priority: str = "normal"
    metadata: dict | None = None


class SupportTicketUpdateRequest(BaseModel):
    status: str = ""
    assignee_user_id: str = ""
    resolution: str = ""


class AppealRecordRequest(BaseModel):
    appeal_id: str = ""
    user_id: str
    appeal_type: str = "general"
    title: str
    detail: dict | None = None
    related_resource_type: str = ""
    related_resource_id: str = ""
    metadata: dict | None = None


class AppealReviewRequest(BaseModel):
    approved: bool = True
    decision_note: str = ""


class PaymentCallbackRequest(BaseModel):
    provider: str
    provider_event_id: str = ""
    provider_order_id: str = ""
    order_id: str = ""
    payment_status: str = "paid"
    amount_cents: int = 0
    payload: dict | None = None


class RiskCaseRequest(BaseModel):
    user_id: str
    case_type: str = "general"
    title: str
    detail: dict | None = None
    severity: str = "medium"
    related_resource_type: str = ""
    related_resource_id: str = ""
    metadata: dict | None = None


class RiskCaseUpdateRequest(BaseModel):
    status: str = ""
    assignee_user_id: str = ""
    resolution: str = ""


class ConfigReleaseRequest(BaseModel):
    release_type: str
    target_id: str
    version_label: str = ""
    change_note: str = ""


class ConfigRollbackRequest(BaseModel):
    change_note: str = ""


@router.get("/config/system")
async def get_system_settings():
    return managed_config_store.get_system_settings()


@router.put("/config/system")
async def update_system_settings(payload: dict, request: Request):
    require_roles(request, OPS_WRITE_ROLES)
    return managed_config_store.update_system_settings(payload or {})


@router.get("/models/strategy")
async def get_model_strategy():
    return managed_config_store.get_model_strategy()


@router.put("/models/strategy")
async def update_model_strategy(payload: dict, request: Request):
    require_roles(request, OPS_WRITE_ROLES)
    return managed_config_store.update_model_strategy(payload or {})


@router.get("/ops/projects")
async def list_ops_projects(request: Request):
    require_roles(request, OPS_VIEW_ROLES)
    return {"projects": project_config_store.list_projects()}


@router.get("/ops/projects/{project_id}")
async def get_ops_project(project_id: str, request: Request):
    require_roles(request, OPS_VIEW_ROLES)
    project = project_config_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/ops/projects")
async def save_ops_project(req: ProjectManifestRequest, request: Request):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        payload = {
            "id": req.project_id,
            "name": req.name,
            "description": req.description,
            "home_route": req.home_route,
            "skills": req.skills,
        }
        if req.pages is not None:
            payload["pages"] = req.pages
        return project_config_store.save_project(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/ops/projects/{project_id}")
async def delete_ops_project(project_id: str, request: Request):
    require_roles(request, OPS_WRITE_ROLES)
    ok = project_config_store.delete_project(project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"project_id": project_id, "deleted": True}


@router.post("/ops/projects/{project_id}/pages")
async def save_ops_project_page(project_id: str, req: ProjectPageRequest, request: Request):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        return project_config_store.upsert_page(
            project_id,
            {
                "id": req.page_id,
                "name": req.name,
                "route": req.route,
                "page": req.page,
                "skills": req.skills,
                "description": req.description,
                "nav_label": req.nav_label,
                "icon": req.icon,
                "visibility": req.visibility,
                "is_home": req.is_home,
                "billing": req.billing,
                "scenario": req.scenario,
            },
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if detail == "PROJECT_NOT_FOUND" else 400
        raise HTTPException(status_code=status_code, detail=detail)


@router.delete("/ops/projects/{project_id}/pages/{page_id}")
async def delete_ops_project_page(project_id: str, page_id: str, request: Request):
    require_roles(request, OPS_WRITE_ROLES)
    project = project_config_store.delete_page(project_id, page_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"project_id": project_id, "page_id": page_id, "project": project}


@router.get("/ops/summary")
async def get_ops_summary(request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_VIEW_ROLES)
    return await AuditOpsService(db).get_ops_summary()


@router.get("/ops/users")
async def list_ops_users(request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_VIEW_ROLES)
    return {"users": await UserOpsService(db).list_users()}


@router.post("/ops/users")
async def upsert_ops_user(req: UserUpsertRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, {"admin"})
    return await UserOpsService(db).upsert_user(
        req.user_id,
        name=req.name,
        email=req.email,
        roles=req.roles,
        status=req.status,
        metadata=req.metadata,
    )


@router.delete("/ops/users/{user_id}")
async def delete_ops_user(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, {"admin"})
    ok = await UserOpsService(db).delete_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user_id, "deleted": True}


@router.post("/ops/users/{user_id}/actions")
async def operate_ops_user(user_id: str, req: UserOperationRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    result = await UserOpsService(db).apply_user_operation(
        user_id,
        action=req.action,
        actor_user_id=get_request_user_id(request),
        credits_delta=req.credits_delta,
        note=req.note,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user_id, "action": req.action, "result": result}


@router.get("/ops/audit-logs")
async def list_ops_audit_logs(
    request: Request,
    resource_type: str = "",
    resource_id: str = "",
    target_user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    return {
        "logs": await AuditOpsService(db).list_ops_audit_logs(
            resource_type=resource_type,
            resource_id=resource_id,
            target_user_id=target_user_id,
            limit=limit,
        )
    }


@router.get("/billing/plans")
async def list_billing_plans(
    request: Request,
    status: str = "",
    plan_type: str = "",
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    return {"plans": await BillingOpsService(db).list_billing_plans(status=status, plan_type=plan_type)}


@router.get("/billing/plans/{plan_id}")
async def get_billing_plan(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_VIEW_ROLES)
    plan = await BillingOpsService(db).get_billing_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.post("/billing/plans")
async def create_billing_plan(req: BillingPlanRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    return await BillingOpsService(db).create_billing_plan(
        req.name,
        plan_id=req.plan_id,
        plan_type=req.plan_type,
        price_cents=req.price_cents,
        currency=req.currency,
        interval=req.interval,
        credits=req.credits,
        features=req.features,
        status=req.status,
        metadata=req.metadata,
    )


@router.delete("/billing/plans/{plan_id}")
async def delete_billing_plan(plan_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    ok = await BillingOpsService(db).delete_billing_plan(plan_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"plan_id": plan_id, "deleted": True}


@router.get("/billing/orders")
async def list_payment_orders(
    request: Request,
    user_id: str = "",
    status: str = "",
    order_type: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {
        "orders": await BillingOpsService(db).list_payment_orders(
            user_id=scoped_user_id if scoped_user_id != "default" else "",
            status=status,
            order_type=order_type,
            limit=limit,
        )
    }


@router.get("/billing/orders/{order_id}")
async def get_payment_order(order_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_VIEW_ROLES)
    order = await BillingOpsService(db).get_payment_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    _assert_owner_or_ops(request, str(order.get("user_id") or ""))
    return order


@router.post("/billing/orders")
async def create_payment_order(req: PaymentOrderRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    return await BillingOpsService(db).create_payment_order(
        req.user_id,
        order_id=req.order_id,
        plan_id=req.plan_id,
        order_type=req.order_type,
        amount_cents=req.amount_cents,
        currency=req.currency,
        status=req.status,
        provider=req.provider,
        provider_order_id=req.provider_order_id,
        credits_delta=req.credits_delta,
        detail=req.detail,
        paid_at=req.paid_at,
    )


@router.post("/billing/orders/{order_id}/refund")
async def refund_payment_order(
    order_id: str,
    req: RefundOrderRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    order = await BillingOpsService(db).refund_payment_order(
        order_id,
        actor_user_id=get_request_user_id(request),
        reason=req.reason,
    )
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@router.get("/billing/callback-events")
async def list_payment_callback_events(
    request: Request,
    provider: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    return {"events": await BillingOpsService(db).list_payment_callback_events(provider=provider, limit=limit)}


@router.post("/billing/callbacks/manual")
async def process_payment_callback(req: PaymentCallbackRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        return await BillingOpsService(db).process_payment_callback(
            provider=req.provider,
            provider_order_id=req.provider_order_id,
            order_id=req.order_id,
            provider_event_id=req.provider_event_id,
            payment_status=req.payment_status,
            amount_cents=req.amount_cents,
            payload=req.payload,
            actor_user_id=get_request_user_id(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/billing/entitlements")
async def list_user_entitlements(
    request: Request,
    subscription_status: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    return {
        "entitlements": await BillingOpsService(db).list_user_entitlements(
            subscription_status=subscription_status,
            limit=limit,
        )
    }


@router.get("/billing/ledger")
async def list_entitlement_ledger(
    request: Request,
    user_id: str = "",
    source_type: str = "",
    source_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {
        "entries": await BillingOpsService(db).list_entitlement_ledger(
            user_id=scoped_user_id if scoped_user_id != "default" else "",
            source_type=source_type,
            source_id=source_id,
            limit=limit,
        )
    }


@router.get("/growth/coupons")
async def list_growth_coupons(
    request: Request,
    status: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    return {"coupons": await GrowthOpsService(db).list_growth_coupons(status=status, limit=limit)}


@router.post("/growth/coupons")
async def create_growth_coupon(req: GrowthCouponRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    service = GrowthOpsService(db)
    coupon = await service.create_growth_coupon(
        code=req.code,
        name=req.name,
        credits_bonus=req.credits_bonus,
        status=req.status,
        max_redemptions=req.max_redemptions,
        starts_at=req.starts_at,
        ends_at=req.ends_at,
        metadata=req.metadata,
    )
    await service.create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="create_growth_coupon",
        resource_type="growth_coupon",
        resource_id=str(coupon.get("id") or coupon.get("code") or ""),
        detail={"code": req.code, "credits_bonus": req.credits_bonus},
    )
    return coupon


@router.post("/growth/coupons/redeem")
async def redeem_growth_coupon(req: RedeemCouponRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        return await GrowthOpsService(db).redeem_growth_coupon(
            req.code,
            user_id=req.user_id,
            actor_user_id=get_request_user_id(request),
            metadata=req.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/growth/trials")
async def list_trial_grants(
    request: Request,
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {"grants": await GrowthOpsService(db).list_trial_grants(user_id=scoped_user_id if scoped_user_id != "default" else "", limit=limit)}


@router.post("/growth/trials")
async def create_trial_grant(req: TrialGrantRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    return await GrowthOpsService(db).create_trial_grant(
        user_id=req.user_id,
        credits_amount=req.credits_amount,
        actor_user_id=get_request_user_id(request),
        reason=req.reason,
        metadata=req.metadata,
    )


@router.get("/billing/entitlements/{user_id}")
async def get_user_entitlement(user_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_VIEW_ROLES)
    entitlement = await BillingOpsService(db).get_user_entitlement(user_id)
    if entitlement is None:
        raise HTTPException(status_code=404, detail="Entitlement not found")
    _assert_owner_or_ops(request, user_id)
    return entitlement


@router.post("/billing/entitlements")
async def upsert_user_entitlement(req: UserEntitlementRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    service = BillingOpsService(db)
    previous = await service.get_user_entitlement(req.user_id)
    result = await service.upsert_user_entitlement(
        req.user_id,
        active_plan_id=req.active_plan_id,
        subscription_status=req.subscription_status,
        credits_balance=req.credits_balance,
        credits_granted_total=req.credits_granted_total,
        credits_used_total=req.credits_used_total,
        expires_at=req.expires_at,
        metadata=req.metadata,
    )
    before_balance = int((previous or {}).get("credits_balance") or 0)
    after_balance = int(result.get("credits_balance") or 0)
    if before_balance != after_balance:
        await service.create_entitlement_ledger_entry(
            req.user_id,
            change_type="manual_adjustment",
            delta_credits=after_balance - before_balance,
            balance_after=after_balance,
            source_type="admin_console",
            source_id=req.user_id,
            operator_user_id=get_request_user_id(request),
            reason="manual_entitlement_update",
        )
    await service.create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="upsert_user_entitlement",
        resource_type="user_entitlement",
        resource_id=req.user_id,
        target_user_id=req.user_id,
        detail={
            "before_balance": before_balance,
            "after_balance": after_balance,
            "subscription_status": req.subscription_status,
            "active_plan_id": req.active_plan_id,
        },
    )
    return result


@router.get("/templates")
async def list_templates(project_id: str = "", category: str = "", db: AsyncSession = Depends(get_db)):
    return {"templates": await ContentOpsService(db).list_templates(project_id=project_id, category=category)}


@router.get("/templates/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    template = await ContentOpsService(db).get_template(template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/templates")
async def create_template(req: TemplateRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    return await ContentOpsService(db).create_template(
        req.name,
        req.content,
        template_id=req.template_id,
        category=req.category,
        project_id=req.project_id,
        scope=req.scope,
        variables=req.variables,
        metadata=req.metadata,
    )


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    ok = await ContentOpsService(db).delete_template(template_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template_id": template_id, "deleted": True}


@router.post("/templates/render")
async def render_template(template_id: str, variables: dict | None = None, db: AsyncSession = Depends(get_db)):
    templates = await ContentOpsService(db).list_templates()
    template = next((item for item in templates if item["id"] == template_id), None)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return {
        "template_id": template_id,
        "content": ContentOpsService.render_template_content(template["content"], variables or {}),
    }


@router.get("/knowledge")
async def list_knowledge(
    project_id: str = "",
    doc_type: str = "",
    tag: str = "",
    db: AsyncSession = Depends(get_db),
):
    return {
        "documents": await ContentOpsService(db).list_knowledge_documents(
            project_id=project_id,
            doc_type=doc_type,
            tag=tag,
        )
    }


@router.get("/knowledge/{document_id}")
async def get_knowledge(document_id: str, db: AsyncSession = Depends(get_db)):
    document = await ContentOpsService(db).get_knowledge_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    return document


@router.post("/knowledge")
async def create_knowledge(req: KnowledgeDocumentRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    return await ContentOpsService(db).create_knowledge_document(
        req.title,
        req.content,
        project_id=req.project_id,
        document_id=req.document_id,
        doc_type=req.doc_type,
        tags=req.tags,
        metadata=req.metadata,
    )


@router.delete("/knowledge/{document_id}")
async def delete_knowledge(document_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    ok = await ContentOpsService(db).delete_knowledge_document(document_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Knowledge document not found")
    return {"document_id": document_id, "deleted": True}


@router.get("/knowledge/context")
async def build_knowledge_context(
    project_id: str,
    task: str = "",
    doc_type: str = "",
    tag: str = "",
    limit: int = settings.knowledge_default_limit,
    db: AsyncSession = Depends(get_db),
):
    return await ContentOpsService(db).build_knowledge_context(
        project_id,
        query=task,
        limit=limit,
        doc_type=doc_type,
        tag=tag,
    )


@router.get("/records")
async def list_product_records(
    request: Request,
    project_id: str = "",
    record_type: str = "",
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {
        "records": await ContentOpsService(db).list_product_records(
            project_id=project_id,
            record_type=record_type,
            user_id=scoped_user_id if scoped_user_id != "default" else "",
            limit=limit,
        )
    }


@router.get("/records/{record_id}")
async def get_product_record(record_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    record = await ContentOpsService(db).get_product_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    _assert_owner_or_ops(request, str(record.get("user_id") or ""))
    return record


@router.post("/records")
async def create_product_record(req: ProductRecordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    return await ContentOpsService(db).create_product_record(
        req.project_id,
        req.record_type,
        req.title,
        req.payload,
        record_id=req.record_id,
        user_id=get_request_user_id(request),
        status=req.status,
    )


@router.get("/results")
async def list_result_records(
    request: Request,
    project_id: str = "",
    page_id: str = "",
    workflow_id: str = "",
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {
        "results": await ContentOpsService(db).list_result_records(
            project_id=project_id,
            page_id=page_id,
            user_id=scoped_user_id if scoped_user_id != "default" else "",
            workflow_id=workflow_id,
            limit=limit,
        )
    }


@router.get("/results/{workflow_id}")
async def get_result_record(workflow_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    scoped_user_id = _resolve_user_scope(request)
    record = await ContentOpsService(db).get_result_record(
        workflow_id,
        user_id=scoped_user_id if scoped_user_id != "default" else "",
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return record


@router.get("/jobs")
async def list_async_jobs(
    request: Request,
    status: str = "",
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    scoped_user_id = _resolve_user_scope(request, user_id)
    jobs = await WorkflowOpsService(db).list_async_jobs(
        status=status,
        user_id=scoped_user_id if scoped_user_id != "default" else "",
        limit=limit,
    )
    return {"jobs": [_serialize_async_job(job) for job in jobs]}


@router.get("/jobs/{job_id}")
async def get_async_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    scoped_user_id = _resolve_user_scope(request)
    job = await WorkflowOpsService(db).get_async_job(
        job_id,
        user_id=scoped_user_id if scoped_user_id != "default" else "",
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_async_job(job)


@router.post("/jobs/{job_id}/cancel")
async def cancel_async_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    ok = await async_job_manager.cancel(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job cannot be cancelled")
    await WorkflowOpsService(db).create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="cancel_job",
        resource_type="async_job",
        resource_id=job_id,
    )
    return {"job_id": job_id, "status": "cancelled"}


@router.post("/jobs/{job_id}/retry")
async def retry_async_job(job_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    job = await WorkflowOpsService(db).retry_async_job(job_id, actor_user_id=get_request_user_id(request))
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/results/{workflow_id}/refund")
async def refund_workflow_charge(
    workflow_id: str,
    req: RefundWorkflowRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        result = await WorkflowOpsService(db).refund_workflow_charge(
            workflow_id,
            actor_user_id=get_request_user_id(request),
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Result not found")
    return result


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status: str = "",
    project_id: str = "",
    page_id: str = "",
    workflow_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    scoped_user_id = "" if _has_any_role(request, OPS_VIEW_ROLES) or _is_dev_open_mode() else get_request_user_id(request)
    return {
        "approvals": await SupportOpsService(db).list_approval_requests(
            status=status,
            project_id=project_id,
            page_id=page_id,
            requester_user_id=scoped_user_id,
            workflow_id=workflow_id,
            limit=limit,
        )
    }


@router.get("/support/tickets")
async def list_support_tickets(
    request: Request,
    status: str = "",
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {"tickets": await SupportOpsService(db).list_support_tickets(status=status, user_id=scoped_user_id if scoped_user_id != "default" else "", limit=limit)}


@router.post("/support/tickets")
async def create_support_ticket(req: SupportTicketRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    service = SupportOpsService(db)
    ticket = await service.create_support_ticket(
        user_id=req.user_id,
        ticket_type=req.ticket_type,
        title=req.title,
        detail=req.detail,
        priority=req.priority,
        metadata=req.metadata,
    )
    await service.create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="create_support_ticket",
        resource_type="support_ticket",
        resource_id=str(ticket.get("id") or ""),
        target_user_id=req.user_id,
        detail={"ticket_type": req.ticket_type, "priority": req.priority},
    )
    return ticket


@router.post("/support/tickets/{ticket_id}")
async def update_support_ticket(
    ticket_id: str,
    req: SupportTicketUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    ticket = await SupportOpsService(db).update_support_ticket(
        ticket_id,
        status=req.status,
        assignee_user_id=req.assignee_user_id,
        resolution=req.resolution,
        actor_user_id=get_request_user_id(request),
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.get("/support/appeals")
async def list_appeal_records(
    request: Request,
    status: str = "",
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {"appeals": await SupportOpsService(db).list_appeal_records(status=status, user_id=scoped_user_id if scoped_user_id != "default" else "", limit=limit)}


@router.post("/support/appeals")
async def create_appeal_record(req: AppealRecordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    service = SupportOpsService(db)
    appeal = await service.create_appeal_record(
        user_id=req.user_id,
        appeal_type=req.appeal_type,
        title=req.title,
        detail=req.detail,
        related_resource_type=req.related_resource_type,
        related_resource_id=req.related_resource_id,
        metadata=req.metadata,
    )
    await service.create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="create_appeal",
        resource_type="appeal_record",
        resource_id=str(appeal.get("id") or ""),
        target_user_id=req.user_id,
        detail={"appeal_type": req.appeal_type},
    )
    return appeal


@router.post("/support/appeals/{appeal_id}/review")
async def review_appeal_record(
    appeal_id: str,
    req: AppealReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    appeal = await SupportOpsService(db).review_appeal_record(
        appeal_id,
        approved=req.approved,
        reviewer_user_id=get_request_user_id(request),
        decision_note=req.decision_note,
    )
    if appeal is None:
        raise HTTPException(status_code=404, detail="Appeal not found")
    return appeal


@router.get("/support/risk-cases")
async def list_risk_cases(
    request: Request,
    status: str = "",
    user_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    scoped_user_id = _resolve_user_scope(request, user_id)
    return {"cases": await SupportOpsService(db).list_risk_cases(status=status, user_id=scoped_user_id if scoped_user_id != "default" else "", limit=limit)}


@router.post("/support/risk-cases")
async def create_risk_case(req: RiskCaseRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    service = SupportOpsService(db)
    risk_case = await service.create_risk_case(
        user_id=req.user_id,
        case_type=req.case_type,
        title=req.title,
        detail=req.detail,
        severity=req.severity,
        related_resource_type=req.related_resource_type,
        related_resource_id=req.related_resource_id,
        metadata=req.metadata,
    )
    await service.create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="create_risk_case",
        resource_type="risk_case",
        resource_id=str(risk_case.get("id") or ""),
        target_user_id=req.user_id,
        detail={"case_type": req.case_type, "severity": req.severity},
    )
    return risk_case


@router.post("/support/risk-cases/{case_id}")
async def update_risk_case(
    case_id: str,
    req: RiskCaseUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    risk_case = await SupportOpsService(db).update_risk_case(
        case_id,
        status=req.status,
        assignee_user_id=req.assignee_user_id,
        resolution=req.resolution,
        actor_user_id=get_request_user_id(request),
    )
    if risk_case is None:
        raise HTTPException(status_code=404, detail="Risk case not found")
    return risk_case


@router.get("/config/releases")
async def list_config_releases(
    request: Request,
    release_type: str = "",
    target_id: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_VIEW_ROLES)
    return {"releases": await AuditOpsService(db).list_config_releases(release_type=release_type, target_id=target_id, limit=limit)}


@router.post("/config/releases")
async def publish_config_release(req: ConfigReleaseRequest, request: Request, db: AsyncSession = Depends(get_db)):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        return await AuditOpsService(db).publish_config_release(
            release_type=req.release_type,
            target_id=req.target_id,
            actor_user_id=get_request_user_id(request),
            version_label=req.version_label,
            change_note=req.change_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/config/releases/{release_id}/rollback")
async def rollback_config_release(
    release_id: str,
    req: ConfigRollbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    try:
        release = await AuditOpsService(db).rollback_config_release(
            release_id,
            actor_user_id=get_request_user_id(request),
            change_note=req.change_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if release is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return release


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    approval = await SupportOpsService(db).get_approval_request(approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    _assert_owner_or_ops(request, str(approval.get("requester_user_id") or ""))
    return approval


@router.post("/approvals/{approval_id}/review")
async def review_approval(
    approval_id: str,
    req: ReviewApprovalRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    require_roles(request, OPS_WRITE_ROLES)
    service = SupportOpsService(db)
    approval = await service.review_approval_request(
        approval_id,
        approved=req.approved,
        reviewer_user_id=get_request_user_id(request),
        comment=req.comment,
    )
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    workflow_status = "awaiting_approval"
    resume_job = None

    if req.approved:
        approvals = await service.list_approval_requests(workflow_id=str(approval.get("workflow_id") or ""), limit=100)
        pending = [item for item in approvals if item.get("status") == "pending"]
        if not pending:
            detail = approval.get("detail") or {}
            step_states = {
                item.get("step_id", ""): {
                    "approved": True,
                    "reviewer_user_id": item.get("reviewer_user_id", ""),
                    "comment": item.get("review_comment", ""),
                }
                for item in approvals
                if item.get("status") == "approved" and item.get("step_id")
            }
            existing_context = detail.get("context") or {}
            checkpoint = detail.get("checkpoint") or {}
            job = await async_job_manager.enqueue(
                "workflow_resume",
                {
                    "workflow_id": approval.get("workflow_id", ""),
                    "task": detail.get("task", ""),
                    "user_id": approval.get("requester_user_id", "default"),
                    "mode": detail.get("mode", "auto"),
                    "context": {
                        **existing_context,
                        "_approvals": step_states,
                        "_approval_parent_workflow_id": approval.get("workflow_id", ""),
                        "_scenario_checkpoint": checkpoint,
                    },
                    "project_id": approval.get("project_id", ""),
                    "page_id": approval.get("page_id", ""),
                },
                priority=settings.async_jobs_default_priority,
            )
            resume_job = {"job_id": job["job_id"], "status": job["status"]}
            workflow_status = "resumed"
    else:
        workflow_status = "rejected"

    from sqlalchemy import text

    await db.execute(
        text("UPDATE agent_workflows SET status = :status WHERE id = :id"),
        {"id": approval.get("workflow_id", ""), "status": workflow_status},
    )
    await db.commit()
    await service.create_ops_audit_log(
        actor_user_id=get_request_user_id(request),
        action="review_approval",
        resource_type="approval",
        resource_id=approval_id,
        target_user_id=str(approval.get("requester_user_id") or ""),
        detail={"approved": req.approved, "comment": req.comment},
    )

    return {
        "approval": approval,
        "workflow_status": workflow_status,
        "resume_job": resume_job,
        "workflow_id": approval.get("workflow_id", ""),
    }
