"""Universal message envelope and shared Pydantic schemas."""

import uuid as uuid_mod
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class PipelineMessage(BaseModel):
    """Message envelope carried through every RabbitMQ queue in the pipeline."""

    # Identity
    request_id: UUID
    trace_id: UUID = Field(default_factory=uuid_mod.uuid4)

    # Workflow
    workflow_name: str = "default"
    current_stage: Optional[str] = None
    deadline_utc: Optional[datetime] = None

    # Page-level context (set by splitter, carried through page stages)
    page_index: Optional[int] = None
    page_count: Optional[int] = None
    file_index: Optional[int] = None

    # Document-level context (set by classification aggregator)
    document_id: Optional[UUID] = None
    document_count: Optional[int] = None

    # Flexible payload for stage-specific data
    payload: dict[str, Any] = Field(default_factory=dict)

    # Tracing
    source_component: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class JobStatusResponse(BaseModel):
    """Response for GET /status/{request_id}."""

    request_id: UUID
    status: str
    workflow_name: str
    created_at: datetime
    deadline_utc: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    page_count: Optional[int] = None
    document_count: Optional[int] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class ProcessResponse(BaseModel):
    """Response for POST /process."""

    request_id: UUID
    status: str = "received"
