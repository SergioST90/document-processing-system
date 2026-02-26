"""Component dispatcher: selects and runs the appropriate component based on DOCPROC_COMPONENT_NAME."""

import asyncio
import importlib
import sys

from config.logging import setup_logging
from config.settings import Settings

COMPONENT_REGISTRY: dict[str, str] = {
    "workflow_router": "src.components.workflow_router.component.WorkflowRouterComponent",
    "splitter": "src.components.splitter.component.SplitterComponent",
    "ocr": "src.components.ocr.component.OCRComponent",
    "classifier": "src.components.classifier.component.ClassifierComponent",
    "classification_aggregator": "src.components.classification_aggregator.component.ClassificationAggregatorComponent",
    "extractor": "src.components.extractor.component.ExtractorComponent",
    "extraction_aggregator": "src.components.extraction_aggregator.component.ExtractionAggregatorComponent",
    "consolidator": "src.components.consolidator.component.ConsolidatorComponent",
    "sla_monitor": "src.components.sla_monitor.component.SLAMonitorComponent",
}


def main() -> None:
    setup_logging()
    settings = Settings()
    component_name = settings.component_name

    if component_name not in COMPONENT_REGISTRY:
        print(f"Unknown component: '{component_name}'. Available: {list(COMPONENT_REGISTRY.keys())}")
        sys.exit(1)

    import_path = COMPONENT_REGISTRY[component_name]
    module_path, class_name = import_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    component_class = getattr(module, class_name)

    component = component_class(settings)
    asyncio.run(component.run())


if __name__ == "__main__":
    main()
