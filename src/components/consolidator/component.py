"""Consolidator: assembles final result from all extracted documents."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import Document, Request
from src.core.schemas import PipelineMessage


class ConsolidatorComponent(BaseComponent):

    component_name = "consolidator"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        request_id = message.request_id

        # Load request and all its documents
        req_result = await session.execute(select(Request).where(Request.id == request_id))
        request = req_result.scalar_one()

        docs_result = await session.execute(
            select(Document)
            .where(Document.request_id == request_id)
            .order_by(Document.created_at)
        )
        documents = list(docs_result.scalars().all())

        # Assemble the final result payload
        result_payload = {
            "request_id": str(request_id),
            "workflow": request.workflow_name,
            "total_pages": request.page_count,
            "total_documents": len(documents),
            "documents": [],
        }

        for doc in documents:
            result_payload["documents"].append({
                "document_id": str(doc.id),
                "doc_type": doc.doc_type,
                "page_indices": doc.page_indices,
                "extracted_data": doc.extracted_data or {},
                "extraction_confidence": doc.extraction_confidence,
                "status": doc.status,
            })
            doc.status = "completed"
            doc.updated_at = datetime.now(timezone.utc)

        # Update request as completed
        request.result_payload = result_payload
        request.status = "completed"
        request.completed_at = datetime.now(timezone.utc)
        request.updated_at = datetime.now(timezone.utc)

        self.logger.info(
            "consolidation_complete",
            request_id=str(request_id),
            documents=len(documents),
            total_pages=request.page_count,
        )

        # Terminal stage: no outgoing messages
        return []
