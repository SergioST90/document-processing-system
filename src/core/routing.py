"""Sentinel routing keys and workflow-aware routing resolution."""

from __future__ import annotations

from src.core.schemas import PipelineMessage
from src.core.workflow_loader import WorkflowLoader

# Sentinel routing keys returned by components
NEXT = "__next__"
BACKOFFICE = "__backoffice__"


def resolve_routing(
    sentinel: str,
    message: PipelineMessage,
    workflow_loader: WorkflowLoader,
    component_name: str,
) -> tuple[str, str, PipelineMessage] | None:
    """Resolve a sentinel routing key to a concrete (exchange, routing_key, message).

    Returns ``None`` when the sentinel is :data:`NEXT` but the current stage is
    terminal (last stage in the workflow).

    For non-sentinel routing keys the message is returned unchanged (backward
    compatibility).
    """
    workflow_name = message.workflow_name
    current_stage_name = message.current_stage

    if current_stage_name is None:
        # Fallback: infer current stage from component_name
        stage = workflow_loader.get_stage_by_component(workflow_name, component_name)
        current_stage_name = stage.name

    if sentinel == NEXT:
        next_stage = workflow_loader.get_next_stage(workflow_name, current_stage_name)
        if next_stage is None:
            return None  # Terminal stage, nothing to publish
        updated_msg = message.model_copy(update={"current_stage": next_stage.name})
        return ("doc.direct", next_stage.routing_key, updated_msg)

    if sentinel == BACKOFFICE:
        stage = workflow_loader.get_stage(workflow_name, current_stage_name)
        if not stage.backoffice_queue:
            raise ValueError(
                f"Stage '{current_stage_name}' has no backoffice_queue configured "
                f"but component tried to route to {BACKOFFICE}"
            )
        return ("doc.backoffice", stage.backoffice_queue, message)

    # Not a sentinel -- pass through as-is
    return ("doc.direct", sentinel, message)
