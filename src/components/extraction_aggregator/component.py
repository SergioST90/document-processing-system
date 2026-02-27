"""Extraction Aggregator: fan-in for extracted documents, triggers consolidation."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.schemas import PipelineMessage


class ExtractionAggregatorComponent(BaseComponent):

    component_name = "extraction_aggregator"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        request_id = message.request_id

        # Atomically increment received_count
        result = await session.execute(
            text("""
                UPDATE aggregation_state
                SET received_count = received_count + 1,
                    updated_at = NOW()
                WHERE request_id = :request_id AND stage = 'extraction'
                RETURNING received_count, expected_count
            """),
            {"request_id": str(request_id)},
        )
        row = result.fetchone()
        if not row:
            self.logger.error("aggregation_state_not_found", request_id=str(request_id), stage="extraction")
            return []

        received, expected = row[0], row[1]
        self.logger.info(
            "extraction_progress",
            request_id=str(request_id),
            received=received,
            expected=expected,
        )

        if received < expected:
            return []

        # All documents extracted! Mark complete and send to consolidation
        await session.execute(
            text("""
                UPDATE aggregation_state
                SET is_complete = true, updated_at = NOW()
                WHERE request_id = :request_id AND stage = 'extraction'
            """),
            {"request_id": str(request_id)},
        )

        self.logger.info("extraction_aggregation_complete", request_id=str(request_id))

        out_message = message.model_copy(
            update={
                "source_component": self.component_name,
            }
        )
        return [("__next__", out_message)]
