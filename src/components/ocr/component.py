"""OCR: extracts text from a page image (STUB implementation)."""

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.base_component import BaseComponent
from src.core.models import Page
from src.core.schemas import PipelineMessage

# Stub OCR text samples
STUB_TEXTS = [
    "FACTURA\nNúmero: F-2024-00142\nFecha: 15/01/2024\nEmisor: Empresa ABC S.L.\nCIF: B12345678\nImporte total: 1.250,00 EUR",
    "DOCUMENTO NACIONAL DE IDENTIDAD\nNombre: Juan García López\nNúmero: 12345678Z\nFecha de nacimiento: 15/03/1985\nFecha de expedición: 01/06/2020",
    "NÓMINA\nEmpresa: TechCorp S.A.\nTrabajador: María Fernández\nPeriodo: Enero 2024\nSalario bruto: 3.200,00 EUR\nSalario neto: 2.450,00 EUR",
    "RECIBO\nComercio: Supermercado XYZ\nFecha: 20/01/2024\nTotal: 47,85 EUR\nForma de pago: Tarjeta",
    "CONTRATO DE TRABAJO\nEmpresa: InnovateTech S.L.\nTrabajador: Carlos Ruiz\nFecha inicio: 01/02/2024\nTipo: Indefinido",
]


class OCRComponent(BaseComponent):

    component_name = "ocr"

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        # --- STUB: Return mock OCR text ---
        ocr_text = random.choice(STUB_TEXTS)
        ocr_confidence = round(random.uniform(0.85, 0.99), 2)

        # Update page in DB
        result = await session.execute(
            select(Page).where(
                Page.request_id == message.request_id,
                Page.page_index == message.page_index,
            )
        )
        page = result.scalar_one()
        page.ocr_text = ocr_text
        page.ocr_confidence = ocr_confidence
        page.status = "ocr_complete"
        page.updated_at = datetime.now(timezone.utc)

        self.logger.info(
            "ocr_complete",
            request_id=str(message.request_id),
            page_index=message.page_index,
            confidence=ocr_confidence,
        )

        # Forward to classifier
        out_message = message.model_copy(
            update={
                "source_component": self.component_name,
                "payload": {
                    **message.payload,
                    "ocr_text": ocr_text,
                    "ocr_confidence": ocr_confidence,
                },
            }
        )
        return [("page.classify", out_message)]
