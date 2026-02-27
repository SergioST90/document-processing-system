"""YAML workflow configuration loader."""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class AggregationConfig(BaseModel):
    type: str  # "fan_in"
    collect_by: str  # "request_id"
    expect_count_from: str  # "page_count" or "document_count"


class StageConfig(BaseModel):
    name: str
    component: str
    routing_key: str
    timeout_seconds: int = 30
    parallel: bool = False
    confidence_threshold: Optional[float] = None
    backoffice_queue: Optional[str] = None
    aggregation: Optional[AggregationConfig] = None


class SLAConfig(BaseModel):
    deadline_seconds: int
    warn_threshold_pct: int = 70
    escalation_threshold_pct: int = 90


class FieldConfig(BaseModel):
    name: str
    type: str
    required: bool = False


class ExtractionSchemaConfig(BaseModel):
    fields: list[FieldConfig]


class WorkflowConfig(BaseModel):
    name: str
    description: str
    version: int
    sla: SLAConfig
    stages: list[StageConfig]
    extraction_schemas: dict[str, ExtractionSchemaConfig] = {}


class WorkflowLoader:
    """Loads and caches workflow YAML configurations."""

    def __init__(self, config_dir: str = "config/workflows"):
        self._config_dir = Path(config_dir)
        self._cache: dict[str, WorkflowConfig] = {}

    def load(self, workflow_name: str) -> WorkflowConfig:
        if workflow_name not in self._cache:
            path = self._config_dir / f"{workflow_name}.yaml"
            if not path.exists():
                raise FileNotFoundError(f"Workflow config not found: {path}")
            with open(path) as f:
                data = yaml.safe_load(f)
            self._cache[workflow_name] = WorkflowConfig(**data)
        return self._cache[workflow_name]

    def get_stage(self, workflow_name: str, stage_name: str) -> StageConfig:
        wf = self.load(workflow_name)
        for stage in wf.stages:
            if stage.name == stage_name:
                return stage
        raise ValueError(f"Stage '{stage_name}' not found in workflow '{workflow_name}'")

    def get_first_stage(self, workflow_name: str) -> StageConfig:
        """Return the first stage of the workflow."""
        wf = self.load(workflow_name)
        if not wf.stages:
            raise ValueError(f"Workflow '{workflow_name}' has no stages")
        return wf.stages[0]

    def get_next_stage(self, workflow_name: str, current_stage_name: str) -> StageConfig | None:
        """Return the stage that follows current_stage_name, or None if terminal."""
        wf = self.load(workflow_name)
        for i, stage in enumerate(wf.stages):
            if stage.name == current_stage_name:
                if i + 1 < len(wf.stages):
                    return wf.stages[i + 1]
                return None
        raise ValueError(f"Stage '{current_stage_name}' not found in workflow '{workflow_name}'")

    def get_stage_by_component(self, workflow_name: str, component_name: str) -> StageConfig:
        """Find the stage that runs a given component (fallback for messages without current_stage)."""
        wf = self.load(workflow_name)
        for stage in wf.stages:
            if stage.component == component_name:
                return stage
        raise ValueError(f"No stage with component '{component_name}' in workflow '{workflow_name}'")

    def get_extraction_schema(self, workflow_name: str, doc_type: str) -> ExtractionSchemaConfig | None:
        wf = self.load(workflow_name)
        return wf.extraction_schemas.get(doc_type)
