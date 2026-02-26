"""Classifier: classifies page by document type (STUB implementation).

Routes low-confidence classifications to back office.
"""

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import BackofficeTask, Page
from src.core.schemas import PipelineMessage

DOC_TYPES = ["invoice", "id_card", "payslip", "receipt", "contract"]


class ClassifierComponent(BaseComponent):

    component_name = "classifier"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        ocr_text = message.payload.get("ocr_text", "")

        # --- STUB: Classify based on keywords in OCR text, with random confidence ---
        doc_type = self._stub_classify(ocr_text)
        confidence = round(random.uniform(0.60, 0.99), 2)

        # Update page in DB
        result = await session.execute(
            select(Page).where(
                Page.request_id == message.request_id,
                Page.page_index == message.page_index,
            )
        )
        page = result.scalar_one()
        page.doc_type = doc_type
        page.classification_confidence = confidence
        page.updated_at = datetime.now(timezone.utc)

        threshold = self.settings.classification_confidence_threshold

        if confidence >= threshold:
            # High confidence: proceed automatically
            page.status = "classified"
            self.logger.info(
                "classified_auto",
                request_id=str(message.request_id),
                page_index=message.page_index,
                doc_type=doc_type,
                confidence=confidence,
            )

            out_message = message.model_copy(
                update={
                    "source_component": self.component_name,
                    "payload": {
                        **message.payload,
                        "doc_type": doc_type,
                        "classification_confidence": confidence,
                    },
                }
            )
            return [("page.classified", out_message)]
        else:
            # Low confidence: send to back office
            page.status = "classification_review"
            task = BackofficeTask(
                request_id=message.request_id,
                task_type="classification",
                reference_id=page.id,
                priority=3,
                deadline_utc=message.deadline_utc,
                required_skills=["classification"],
                input_data={
                    "page_index": message.page_index,
                    "ocr_text": ocr_text,
                    "suggested_type": doc_type,
                    "confidence": confidence,
                },
            )
            session.add(task)

            self.logger.info(
                "classified_manual",
                request_id=str(message.request_id),
                page_index=message.page_index,
                suggested_type=doc_type,
                confidence=confidence,
            )

            bo_message = message.model_copy(
                update={
                    "source_component": self.component_name,
                    "payload": {
                        **message.payload,
                        "task_id": str(task.id),
                        "doc_type": doc_type,
                        "classification_confidence": confidence,
                    },
                }
            )
            await self.publish_to_backoffice("task.classification", bo_message)
            return []  # No automatic downstream; backoffice will publish when done

    def _stub_classify(self, ocr_text: str) -> str:
        """Simple keyword-based classification stub."""
        text_lower = ocr_text.lower()
        if "factura" in text_lower or "invoice" in text_lower:
            return "invoice"
        if "nómina" in text_lower or "salario" in text_lower:
            return "payslip"
        if "documento nacional" in text_lower or "dni" in text_lower:
            return "id_card"
        if "recibo" in text_lower:
            return "receipt"
        if "contrato" in text_lower:
            return "contract"
        return random.choice(DOC_TYPES)
