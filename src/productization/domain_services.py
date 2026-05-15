"""Focused service facades over split product-operation domains."""

from __future__ import annotations

from src.productization.audit_ops import AuditOpsMixin
from src.productization.base_service import BaseProductService
from src.productization.billing_ops import BillingOpsMixin
from src.productization.content_ops import ContentOpsMixin
from src.productization.growth_ops import GrowthOpsMixin
from src.productization.support_ops import SupportOpsMixin
from src.productization.user_ops import UserOpsMixin
from src.productization.workflow_ops import WorkflowOpsMixin


class AuditOpsService(AuditOpsMixin, ContentOpsMixin, BaseProductService):
    """Audit logging, summary and config release operations."""


class UserOpsService(UserOpsMixin, BillingOpsMixin, AuditOpsMixin, BaseProductService):
    """User/account lifecycle and identity operations."""


class BillingOpsService(BillingOpsMixin, UserOpsMixin, AuditOpsMixin, BaseProductService):
    """Billing plans, orders, entitlements and credit ledger operations."""


class GrowthOpsService(GrowthOpsMixin, BillingOpsMixin, UserOpsMixin, AuditOpsMixin, BaseProductService):
    """Coupons and trial-grant operations."""


class SupportOpsService(SupportOpsMixin, WorkflowOpsMixin, AuditOpsMixin, BaseProductService):
    """Support tickets, appeals, risk and approval operations."""


class ContentOpsService(ContentOpsMixin, BaseProductService):
    """Templates, knowledge, product records and results."""


class WorkflowOpsService(WorkflowOpsMixin, BillingOpsMixin, ContentOpsMixin, AuditOpsMixin, UserOpsMixin, BaseProductService):
    """Async jobs, workflow refunds and related execution operations."""
