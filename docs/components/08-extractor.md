# Extractor

## Que hace

Recibe un documento logico (una agrupacion de paginas ya clasificadas) y extrae campos de datos estructurados en funcion de su tipo documental. Por ejemplo, de una factura extrae numero de factura, importe, proveedor y fecha; de un DNI extrae nombre, numero y fecha de nacimiento.

Al igual que el Classifier, tiene **routing por confianza**: si la extraccion no alcanza el umbral configurado, se crea una tarea de back office para que un operador humano complete o corrija los datos.

**Fichero**: `src/components/extractor/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.extractor` (routing key `doc.extract`), publicado por el Classification Aggregator.

### Configuracion

```bash
DOCPROC_COMPONENT_NAME=extractor
DOCPROC_EXTRACTION_CONFIDENCE_THRESHOLD=0.75   # Umbral de confianza
```

### Esquemas de extraccion

Los campos a extraer por tipo documental se definen en el YAML del workflow:

```yaml
extraction_schemas:
  invoice:
    fields:
      - name: invoice_number
        type: string
        required: true
      - name: total_amount
        type: decimal
        required: true
      - name: vendor_name
        type: string
      - name: date
        type: date
  id_card:
    fields:
      - name: full_name
        type: string
        required: true
      - name: id_number
        type: string
        required: true
      - name: date_of_birth
        type: date
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.extractor`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Extraccion** (STUB): Lee el `doc_type` del payload y devuelve datos hardcodeados del diccionario `STUB_EXTRACTIONS`. Genera una confianza aleatoria entre 0.65 y 0.99. En produccion, aqui se usaria un modelo ML de extraccion de entidades o un servicio de document understanding.

2. **Actualizacion en BD**: Busca la fila `Document` por `document_id` y actualiza:
   - `extracted_data`: diccionario JSONB con los campos extraidos
   - `extraction_confidence`: confianza global de la extraccion

3. **Decision por confianza**:

   **Camino automatico** (confianza >= umbral):
   - Actualiza `document.status` a `"extracted"`
   - Publica al routing key `"doc.extracted"` (va a `q.extraction_aggregator`)

   **Camino manual** (confianza < umbral):
   - Actualiza `document.status` a `"extraction_review"`
   - Crea una fila `BackofficeTask` con:
     - `task_type`: `"extraction"`
     - `reference_id`: ID del documento
     - `required_skills`: `["extraction", doc_type]` (ej: `["extraction", "invoice"]`)
     - `input_data`: contiene `document_id`, `doc_type`, `extracted_data` (parcial), `confidence` y los textos OCR de todas las paginas del documento
   - Publica al exchange `doc.backoffice` con routing key `"task.extraction"`
   - Devuelve lista vacia (sera el Back Office quien reinyecte al pipeline)

### Mensajes de entrada y salida

**Entrada** (de `q.extractor`):
```json
{
  "request_id": "uuid",
  "document_id": "uuid-doc",
  "document_count": 3,
  "payload": {
    "doc_type": "invoice",
    "page_indices": [0, 1],
    "ocr_texts": {
      "0": "FACTURA\nNumero: F-2024-00142\n...",
      "1": "Lineas de detalle:\n..."
    }
  }
}
```

**Salida - camino automatico** (a `q.extraction_aggregator` via `doc.extracted`):
```json
{
  "request_id": "uuid",
  "document_id": "uuid-doc",
  "source_component": "extractor",
  "payload": {
    "doc_type": "invoice",
    "extracted_data": {
      "invoice_number": "F-2024-00142",
      "total_amount": 1250.00,
      "vendor_name": "Empresa ABC S.L.",
      "date": "2024-01-15"
    },
    "extraction_confidence": 0.91
  }
}
```

**Salida - camino manual** (a `q.backoffice.extraction` via `task.extraction`):
```json
{
  "request_id": "uuid",
  "document_id": "uuid-doc",
  "source_component": "extractor",
  "payload": {
    "task_id": "uuid-tarea",
    "doc_type": "invoice",
    "extracted_data": {
      "invoice_number": "F-2024-00142",
      "total_amount": null,
      "vendor_name": "Empresa ABC S.L."
    },
    "extraction_confidence": 0.68
  }
}
```

### Datos stub por tipo documental

| Tipo | Campos |
|---|---|
| `invoice` | invoice_number, total_amount, vendor_name, date |
| `id_card` | full_name, id_number, date_of_birth |
| `payslip` | company_name, employee_name, gross_amount, net_amount, period |
| `receipt` | merchant, total, date |
| `contract` | company, employee, start_date, type |

### Para implementar la extraccion real

```python
async def setup(self):
    # Cargar modelo de extraccion de entidades
    from transformers import pipeline
    self.ner_pipeline = pipeline("ner", model="my-custom-model")

def _extract(self, doc_type: str, ocr_texts: dict) -> tuple[dict, float]:
    full_text = "\n".join(ocr_texts.values())
    entities = self.ner_pipeline(full_text)
    # Mapear entidades NER a los campos esperados segun el schema del doc_type
    ...
```
