"""Initial schema with all tables.

Revision ID: 001
Revises:
Create Date: 2026-02-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # requests
    op.create_table(
        "requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(255)),
        sa.Column("channel", sa.String(100), nullable=False),
        sa.Column("workflow_name", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="received"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="5"),
        sa.Column("deadline_utc", sa.DateTime(timezone=True)),
        sa.Column("sla_seconds", sa.Integer),
        sa.Column("original_filename", sa.String(500)),
        sa.Column("file_storage_path", sa.String(1000)),
        sa.Column("page_count", sa.Integer),
        sa.Column("document_count", sa.Integer),
        sa.Column("result_payload", postgresql.JSONB),
        sa.Column("error_message", sa.Text),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_requests_status", "requests", ["status"])
    op.create_index(
        "idx_requests_deadline",
        "requests",
        ["deadline_utc"],
        postgresql_where=sa.text("status NOT IN ('completed', 'failed')"),
    )

    # documents (create before pages since pages FK to documents)
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("doc_type", sa.String(100), nullable=False),
        sa.Column("page_indices", postgresql.ARRAY(sa.Integer), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="created"),
        sa.Column("extracted_data", postgresql.JSONB),
        sa.Column("extraction_confidence", sa.Real),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_documents_request", "documents", ["request_id"])

    # pages
    op.create_table(
        "pages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("page_index", sa.Integer, nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="extracted"),
        sa.Column("file_storage_path", sa.String(1000)),
        sa.Column("ocr_text", sa.Text),
        sa.Column("ocr_confidence", sa.Real),
        sa.Column("doc_type", sa.String(100)),
        sa.Column("classification_confidence", sa.Real),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id")),
        sa.Column("metadata", postgresql.JSONB, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_pages_request", "pages", ["request_id"])
    op.create_index("idx_pages_document", "pages", ["document_id"])
    op.create_unique_constraint("uq_pages_request_index", "pages", ["request_id", "page_index"])

    # backoffice_tasks
    op.create_table(
        "backoffice_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="5"),
        sa.Column("assigned_to", sa.String(100)),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("input_data", postgresql.JSONB, nullable=False),
        sa.Column("output_data", postgresql.JSONB),
        sa.Column("deadline_utc", sa.DateTime(timezone=True)),
        sa.Column("required_skills", postgresql.ARRAY(sa.String), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("idx_bo_tasks_status", "backoffice_tasks", ["status", "priority"])
    op.create_index(
        "idx_bo_tasks_assigned",
        "backoffice_tasks",
        ["assigned_to"],
        postgresql_where=sa.text("status = 'assigned'"),
    )
    op.create_index("idx_bo_tasks_request", "backoffice_tasks", ["request_id"])

    # operators
    op.create_table(
        "operators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("display_name", sa.String(200)),
        sa.Column("skills", postgresql.ARRAY(sa.String), server_default="{}"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("current_task_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # aggregation_state
    op.create_table(
        "aggregation_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("expected_count", sa.Integer, nullable=False),
        sa.Column("received_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("received_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True)), server_default="{}"),
        sa.Column("is_complete", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_agg_state_lookup", "aggregation_state", ["request_id", "stage"], unique=True)


def downgrade() -> None:
    op.drop_table("aggregation_state")
    op.drop_table("operators")
    op.drop_table("backoffice_tasks")
    op.drop_table("pages")
    op.drop_table("documents")
    op.drop_table("requests")
