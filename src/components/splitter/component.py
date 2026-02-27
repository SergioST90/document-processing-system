"""Splitter: decompresses files and extracts individual pages (fan-out)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import AggregationState, Page, Request
from src.core.schemas import PipelineMessage


class SplitterComponent(BaseComponent):

    component_name = "splitter"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        file_path = Path(message.payload.get("file_path", ""))

        # --- STUB: Simulate extracting pages from a document ---
        # In production, this would use PyPDF2, pdf2image, Pillow, etc.
        # For now, simulate 3-5 pages per file
        import random

        page_count = random.randint(3, 5)
        self.logger.info(
            "splitting_file",
            request_id=str(message.request_id),
            file_path=str(file_path),
            page_count=page_count,
        )

        # Update request with page count
        result = await session.execute(select(Request).where(Request.id == message.request_id))
        request = result.scalar_one()
        request.page_count = page_count
        request.status = "splitting"
        request.updated_at = datetime.now(timezone.utc)

        # Create aggregation state for classification fan-in
        agg_state = AggregationState(
            request_id=message.request_id,
            stage="classification",
            expected_count=page_count,
        )
        session.add(agg_state)

        # Create page records and fan-out messages
        outgoing: list[tuple[str, PipelineMessage]] = []
        for i in range(page_count):
            # Create page in DB
            page = Page(
                id=uuid.uuid4(),
                request_id=message.request_id,
                page_index=i,
                status="extracted",
                file_storage_path=str(file_path),  # In real impl, each page would have its own path
            )
            session.add(page)

            # Create message for OCR
            page_message = message.model_copy(
                update={
                    "page_index": i,
                    "page_count": page_count,
                    "source_component": self.component_name,
                    "payload": {
                        **message.payload,
                        "page_id": str(page.id),
                        "page_index": i,
                    },
                }
            )
            outgoing.append(("__next__", page_message))

        self.logger.info(
            "split_complete",
            request_id=str(message.request_id),
            pages_created=page_count,
        )
        return outgoing
