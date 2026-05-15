"""Product operations compatibility facade."""

from __future__ import annotations

from src.productization.audit_ops import AuditOpsMixin
from src.productization.base_service import BaseProductService
from src.productization.billing_ops import BillingOpsMixin
from src.productization.content_ops import ContentOpsMixin
from src.productization.growth_ops import GrowthOpsMixin
from src.productization.support_ops import SupportOpsMixin
from src.productization.user_ops import UserOpsMixin
from src.productization.workflow_ops import WorkflowOpsMixin


class ProductOpsService(
    AuditOpsMixin,
    GrowthOpsMixin,
    SupportOpsMixin,
    BillingOpsMixin,
    UserOpsMixin,
    WorkflowOpsMixin,
    ContentOpsMixin,
    BaseProductService,
):
    """Backward-compatible aggregate over the split domain services."""
