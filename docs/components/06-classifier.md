# Classifier

## Que hace

Recibe una pagina con su texto OCR y determina a que tipo de documento pertenece (factura, DNI, nomina, recibo, contrato, etc.). Asigna un nivel de confianza a la clasificacion.

Es el primer componente con **routing por confianza**:
- Si la confianza es **alta** (>= umbral): la pagina continua automaticamente al aggregator.
- Si la confianza es **baja** (< umbral): se crea una tarea de back office y se envía a un operador humano para que la clasifique manualmente.

**Fichero**: `src/components/classifier/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.classifier` (routing key `page.classify`), publicado por el componente OCR.

### Configuracion

```bash
DOCPROC_COMPONENT_NAME=classifier
DOCPROC_CLASSIFICATION_CONFIDENCE_THRESHOLD=0.80   # Umbral de confianza (0.0 a 1.0)
```

El umbral tambien se puede definir por workflow en el YAML:

```yaml
stages:
  - name: classify
    component: classifier
    confidence_threshold: 0.85   # Sobreescribe el umbral global para este flujo
```

### Tipos documentales soportados

En la implementacion stub: `invoice`, `id_card`, `payslip`, `receipt`, `contract`. En produccion, los tipos dependeran del modelo de clasificacion configurado.

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.classifier`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Clasificacion** (STUB): Usa el metodo `_stub_classify()` que hace clasificacion basada en keywords del texto OCR:
   - Si contiene "factura" o "invoice" -> `invoice`
   - Si contiene "nomina" o "salario" -> `payslip`
   - Si contiene "documento nacional" o "dni" -> `id_card`
   - Si contiene "recibo" -> `receipt`
   - Si contiene "contrato" -> `contract`
   - Si no hay match -> tipo aleatorio

   La confianza se genera aleatoriamente entre 0.60 y 0.99.

2. **Actualizacion en BD**: Busca la fila `Page` y actualiza `doc_type` y `classification_confidence`.

3. **Decision por confianza**:

   **Camino automatico** (confianza >= umbral):
   - Actualiza `page.status` a `"classified"`
   - Publica el mensaje al routing key `"page.classified"` (va a `q.classification_aggregator`)

   **Camino manual** (confianza < umbral):
   - Actualiza `page.status` a `"classification_review"`
   - Crea una fila `BackofficeTask` con:
     - `task_type`: `"classification"`
     - `reference_id`: ID de la pagina
     - `priority`: 3
     - `deadline_utc`: heredado del mensaje
     - `required_skills`: `["classification"]`
     - `input_data`: contiene `page_index`, `ocr_text`, `suggested_type` y `confidence`
   - Publica al exchange `doc.backoffice` con routing key `"task.classification"`
   - **No** publica nada al pipeline principal (devuelve lista vacia)
   - Sera el Back Office quien, tras la correccion del operador, publique a `"page.classified"`

### Mensajes de entrada y salida

**Entrada** (de `q.classifier`):
```json
{
  "request_id": "uuid",
  "page_index": 2,
  "payload": {
    "ocr_text": "FACTURA\nNumero: F-2024-00142\n..."
  }
}
```

**Salida - camino automatico** (a `q.classification_aggregator` via `page.classified`):
```json
{
  "request_id": "uuid",
  "page_index": 2,
  "source_component": "classifier",
  "payload": {
    "ocr_text": "...",
    "doc_type": "invoice",
    "classification_confidence": 0.92
  }
}
```

**Salida - camino manual** (a `q.backoffice.classification` via `task.classification`):
```json
{
  "request_id": "uuid",
  "page_index": 2,
  "source_component": "classifier",
  "payload": {
    "task_id": "uuid-tarea",
    "doc_type": "invoice",
    "classification_confidence": 0.65
  }
}
```

### Para implementar la clasificacion real

Reemplazar `_stub_classify()` por un modelo ML. Ejemplo con un clasificador:

```python
async def setup(self):
    import joblib
    self.model = joblib.load("models/classifier_v3.pkl")
    self.vectorizer = joblib.load("models/tfidf_vectorizer.pkl")

def _classify(self, ocr_text: str) -> tuple[str, float]:
    features = self.vectorizer.transform([ocr_text])
    probas = self.model.predict_proba(features)[0]
    predicted_class = self.model.classes_[probas.argmax()]
    confidence = probas.max()
    return predicted_class, confidence
```
