"""Classification Aggregator: fan-in for classified pages, groups into logical documents."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import AggregationState, Document, Page, Request
from src.core.schemas import PipelineMessage


class ClassificationAggregatorComponent(BaseComponent):

    component_name = "classification_aggregator"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        request_id = message.request_id

        # Atomically increment received_count and check completion
        result = await session.execute(
            text("""
                UPDATE aggregation_state
                SET received_count = received_count + 1,
                    updated_at = NOW()
                WHERE request_id = :request_id AND stage = 'classification'
                RETURNING received_count, expected_count
            """),
            {"request_id": str(request_id)},
        )
        row = result.fetchone()
        if not row:
            self.logger.error("aggregation_state_not_found", request_id=str(request_id))
            return []

        received, expected = row[0], row[1]
        self.logger.info(
            "classification_progress",
            request_id=str(request_id),
            received=received,
            expected=expected,
        )

        if received < expected:
            return []  # Still waiting for more pages

        # All pages classified! Mark aggregation complete
        await session.execute(
            text("""
                UPDATE aggregation_state
                SET is_complete = true, updated_at = NOW()
                WHERE request_id = :request_id AND stage = 'classification'
            """),
            {"request_id": str(request_id)},
        )

        # Load all pages and group them into logical documents by doc_type
        pages_result = await session.execute(
            select(Page)
            .where(Page.request_id == request_id)
            .order_by(Page.page_index)
        )
        pages = list(pages_result.scalars().all())

        # Group consecutive pages of the same type into documents
        documents = self._group_pages_into_documents(pages)

        # Create document records and aggregation state for extraction
        doc_count = len(documents)
        request_result = await session.execute(select(Request).where(Request.id == request_id))
        request = request_result.scalar_one()
        request.document_count = doc_count
        request.status = "extracting"
        request.updated_at = datetime.now(timezone.utc)

        # Create extraction aggregation state
        ext_agg = AggregationState(
            request_id=request_id,
            stage="extraction",
            expected_count=doc_count,
        )
        session.add(ext_agg)

        # Create document records and fan-out messages
        outgoing: list[tuple[str, PipelineMessage]] = []
        for doc_type, page_indices in documents:
            doc = Document(
                id=uuid.uuid4(),
                request_id=request_id,
                doc_type=doc_type,
                page_indices=page_indices,
                status="created",
            )
            session.add(doc)

            # Link pages to this document
            for pi in page_indices:
                page = next(p for p in pages if p.page_index == pi)
                page.document_id = doc.id
                page.status = "grouped"
                page.updated_at = datetime.now(timezone.utc)

            # Fan-out: one message per document for extraction
            doc_message = message.model_copy(
                update={
                    "document_id": doc.id,
                    "document_count": doc_count,
                    "source_component": self.component_name,
                    "payload": {
                        "doc_type": doc_type,
                        "page_indices": page_indices,
                        "document_id": str(doc.id),
                        "ocr_texts": {
                            pi: next(p.ocr_text for p in pages if p.page_index == pi)
                            for pi in page_indices
                        },
                    },
                }
            )
            outgoing.append(("doc.extract", doc_message))

        self.logger.info(
            "classification_aggregation_complete",
            request_id=str(request_id),
            documents_created=doc_count,
        )
        return outgoing

    def _group_pages_into_documents(self, pages: list[Page]) -> list[tuple[str, list[int]]]:
        """Group consecutive pages of the same doc_type into logical documents.

        Returns list of (doc_type, [page_indices]).
        """
        if not pages:
            return []

        groups: list[tuple[str, list[int]]] = []
        current_type = pages[0].doc_type or "unknown"
        current_indices = [pages[0].page_index]

        for page in pages[1:]:
            page_type = page.doc_type or "unknown"
            if page_type == current_type:
                current_indices.append(page.page_index)
            else:
                groups.append((current_type, current_indices))
                current_type = page_type
                current_indices = [page.page_index]

        groups.append((current_type, current_indices))
        return groups
