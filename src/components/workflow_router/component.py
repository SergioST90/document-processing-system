"""Workflow Router: determines which workflow to execute and sets SLA deadline."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import Request
from src.core.schemas import PipelineMessage
from src.core.sla import calculate_deadline


class WorkflowRouterComponent(BaseComponent):

    component_name = "workflow_router"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        # Load workflow config
        workflow = self._workflow_loader.load(message.workflow_name)
        deadline = calculate_deadline(workflow.sla.deadline_seconds)

        # Update request in DB
        result = await session.execute(select(Request).where(Request.id == message.request_id))
        request = result.scalar_one_or_none()
        if not request:
            self.logger.error("request_not_found", request_id=str(message.request_id))
            return []

        request.status = "routing"
        request.deadline_utc = deadline
        request.sla_seconds = workflow.sla.deadline_seconds
        request.updated_at = datetime.now(timezone.utc)

        # Resolve first stage from workflow definition
        first_stage = self._workflow_loader.get_first_stage(message.workflow_name)

        self.logger.info(
            "workflow_resolved",
            request_id=str(message.request_id),
            workflow=message.workflow_name,
            sla_seconds=workflow.sla.deadline_seconds,
            first_stage=first_stage.name,
        )

        # Forward to first stage of the workflow
        out_message = message.model_copy(
            update={
                "deadline_utc": deadline,
                "current_stage": first_stage.name,
                "source_component": self.component_name,
            }
        )
        return [(first_stage.routing_key, out_message)]
