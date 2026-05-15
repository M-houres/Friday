"""baseline schema

Revision ID: 20260515_000001
Revises:
Create Date: 2026-05-15 00:00:01
"""

from __future__ import annotations

from alembic import op

from src.db_schema import SCHEMA_STATEMENTS


revision = "20260515_000001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in SCHEMA_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    pass
