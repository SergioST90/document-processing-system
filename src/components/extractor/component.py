"""Extractor: extracts structured data from a document (STUB implementation).

Routes low-confidence extractions to back office.
"""

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import BackofficeTask, Document
from src.core.schemas import PipelineMessage

# Stub extraction results per doc type
STUB_EXTRACTIONS = {
    "invoice": {
        "invoice_number": "F-2024-00142",
        "total_amount": 1250.00,
        "vendor_name": "Empresa ABC S.L.",
        "date": "2024-01-15",
    },
    "id_card": {
        "full_name": "Juan García López",
        "id_number": "12345678Z",
        "date_of_birth": "1985-03-15",
    },
    "payslip": {
        "company_name": "TechCorp S.A.",
        "employee_name": "María Fernández",
        "gross_amount": 3200.00,
        "net_amount": 2450.00,
        "period": "Enero 2024",
    },
    "receipt": {
        "merchant": "Supermercado XYZ",
        "total": 47.85,
        "date": "2024-01-20",
    },
    "contract": {
        "company": "InnovateTech S.L.",
        "employee": "Carlos Ruiz",
        "start_date": "2024-02-01",
        "type": "Indefinido",
    },
}


class ExtractorComponent(BaseComponent):

    component_name = "extractor"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        doc_type = message.payload.get("doc_type", "unknown")
        document_id = message.document_id

        # --- STUB: Return mock extracted data ---
        extracted_data = STUB_EXTRACTIONS.get(doc_type, {"raw_text": "Unrecognized document"})
        confidence = round(random.uniform(0.65, 0.99), 2)

        # Update document in DB
        result = await session.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one()
        doc.extracted_data = extracted_data
        doc.extraction_confidence = confidence
        doc.updated_at = datetime.now(timezone.utc)

        threshold = self.settings.extraction_confidence_threshold

        if confidence >= threshold:
            doc.status = "extracted"
            self.logger.info(
                "extracted_auto",
                request_id=str(message.request_id),
                document_id=str(document_id),
                doc_type=doc_type,
                confidence=confidence,
            )

            out_message = message.model_copy(
                update={
                    "source_component": self.component_name,
                    "payload": {
                        **message.payload,
                        "extracted_data": extracted_data,
                        "extraction_confidence": confidence,
                    },
                }
            )
            return [("__next__", out_message)]
        else:
            doc.status = "extraction_review"
            task = BackofficeTask(
                request_id=message.request_id,
                task_type="extraction",
                reference_id=doc.id,
                priority=3,
                deadline_utc=message.deadline_utc,
                required_skills=["extraction", doc_type],
                source_stage=message.current_stage,
                workflow_name=message.workflow_name,
                input_data={
                    "document_id": str(doc.id),
                    "doc_type": doc_type,
                    "extracted_data": extracted_data,
                    "confidence": confidence,
                    "ocr_texts": message.payload.get("ocr_texts", {}),
                },
            )
            session.add(task)

            self.logger.info(
                "extracted_manual",
                request_id=str(message.request_id),
                document_id=str(document_id),
                doc_type=doc_type,
                confidence=confidence,
            )

            bo_message = message.model_copy(
                update={
                    "source_component": self.component_name,
                    "payload": {
                        **message.payload,
                        "task_id": str(task.id),
                        "extracted_data": extracted_data,
                        "extraction_confidence": confidence,
                    },
                }
            )
            return [("__backoffice__", bo_message)]
