"""Add source_stage and workflow_name to backoffice_tasks for workflow-aware routing.

Revision ID: 002
Revises: 001
Create Date: 2026-02-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("backoffice_tasks", sa.Column("source_stage", sa.String(100)))
    op.add_column("backoffice_tasks", sa.Column("workflow_name", sa.String(100)))


def downgrade() -> None:
    op.drop_column("backoffice_tasks", "workflow_name")
    op.drop_column("backoffice_tasks", "source_stage")
