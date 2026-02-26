"""SQLAlchemy ORM models for all database tables."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Real,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Request(Base):
    """Central tracking table for each client submission."""

    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_id: Mapped[str | None] = mapped_column(String(255))
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="received")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    deadline_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_seconds: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str | None] = mapped_column(String(500))
    file_storage_path: Mapped[str | None] = mapped_column(String(1000))
    page_count: Mapped[int | None] = mapped_column(Integer)
    document_count: Mapped[int | None] = mapped_column(Integer)
    result_payload: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    pages: Mapped[list["Page"]] = relationship(back_populates="request", cascade="all, delete-orphan")
    documents: Mapped[list["Document"]] = relationship(back_populates="request", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_requests_status", "status"),
        Index(
            "idx_requests_deadline",
            "deadline_utc",
            postgresql_where=text("status NOT IN ('completed', 'failed')"),
        ),
    )


class Page(Base):
    """One row per extracted page from a file."""

    __tablename__ = "pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("requests.id"), nullable=False)
    page_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="extracted")
    file_storage_path: Mapped[str | None] = mapped_column(String(1000))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Real)
    doc_type: Mapped[str | None] = mapped_column(String(100))
    classification_confidence: Mapped[float | None] = mapped_column(Real)
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id"))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    request: Mapped["Request"] = relationship(back_populates="pages")

    __table_args__ = (
        Index("idx_pages_request", "request_id"),
        Index("idx_pages_document", "document_id"),
    )


class Document(Base):
    """Logical documents: groups of classified pages."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("requests.id"), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(100), nullable=False)
    page_indices: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="created")
    extracted_data: Mapped[dict | None] = mapped_column(JSONB)
    extraction_confidence: Mapped[float | None] = mapped_column(Real)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    request: Mapped["Request"] = relationship(back_populates="documents")

    __table_args__ = (Index("idx_documents_request", "request_id"),)


class BackofficeTask(Base):
    """Human-in-the-loop tasks for back office operators."""

    __tablename__ = "backoffice_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("requests.id"), nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    assigned_to: Mapped[str | None] = mapped_column(String(100))
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_data: Mapped[dict | None] = mapped_column(JSONB)
    deadline_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    required_skills: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_bo_tasks_status", "status", "priority"),
        Index(
            "idx_bo_tasks_assigned",
            "assigned_to",
            postgresql_where=text("status = 'assigned'"),
        ),
        Index("idx_bo_tasks_request", "request_id"),
    )


class Operator(Base):
    """Back-office operator registry."""

    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    skills: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    current_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )


class AggregationState(Base):
    """Tracks fan-in progress for aggregator components."""

    __tablename__ = "aggregation_state"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("requests.id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    received_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    received_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("idx_agg_state_lookup", "request_id", "stage", unique=True),)
