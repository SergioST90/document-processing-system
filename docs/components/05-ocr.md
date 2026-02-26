# OCR

## Que hace

Recibe una pagina individual y le extrae el texto mediante reconocimiento optico de caracteres. Almacena el texto extraido y su nivel de confianza en la base de datos y reenvía el resultado al clasificador.

Es uno de los componentes mas intensivos en CPU/GPU del pipeline y el principal candidato para escalado horizontal (HPA en Kubernetes).

**Fichero**: `src/components/ocr/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.ocr` (routing key `page.ocr`), publicado por el Splitter. Recibe un mensaje por pagina y los procesa en paralelo.

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=ocr
DOCPROC_STORAGE_PATH=/data/storage   # Para acceder a las imagenes de las paginas
```

### Escalado

Es el componente que mas se beneficia de escalado horizontal:

```yaml
# Kubernetes HPA
minReplicas: 2
maxReplicas: 10
metrics:
  - type: Pods
    pods:
      metric:
        name: rabbitmq_queue_messages_ready
        selector:
          matchLabels:
            queue: q.ocr
      target:
        type: AverageValue
        averageValue: "5"   # Escalar si hay mas de 5 mensajes por pod
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.ocr`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **OCR** (STUB): En la implementacion actual, selecciona aleatoriamente un texto de ejemplo de una lista de 5 muestras (factura, DNI, nomina, recibo, contrato en espanol). Genera una confianza aleatoria entre 0.85 y 0.99.

2. **Actualizacion en BD**: Busca la fila `Page` por `request_id` + `page_index` y actualiza:
   - `ocr_text`: texto extraido
   - `ocr_confidence`: confianza del OCR
   - `status`: de `"extracted"` a `"ocr_complete"`

3. **Publicacion**: Crea una copia del mensaje anadiendo el texto OCR y la confianza al payload. Lo publica con routing key `"page.classify"`.

### Mensajes de entrada y salida

**Entrada** (de `q.ocr`):
```json
{
  "request_id": "uuid",
  "page_index": 2,
  "page_count": 5,
  "payload": {
    "page_id": "uuid-pagina",
    "page_index": 2
  }
}
```

**Salida** (a `q.classifier` via routing key `page.classify`):
```json
{
  "request_id": "uuid",
  "page_index": 2,
  "page_count": 5,
  "source_component": "ocr",
  "payload": {
    "page_id": "uuid-pagina",
    "page_index": 2,
    "ocr_text": "FACTURA\nNumero: F-2024-00142\n...",
    "ocr_confidence": 0.95
  }
}
```

### Para implementar el OCR real

Reemplazar la seccion STUB. Ejemplo con Tesseract:

```python
import pytesseract
from PIL import Image

image = Image.open(page_image_path)
ocr_result = pytesseract.image_to_data(image, lang="spa", output_type=pytesseract.Output.DICT)
ocr_text = pytesseract.image_to_string(image, lang="spa")
# Calcular confianza media de los bloques detectados
confidences = [c for c in ocr_result["conf"] if c > 0]
ocr_confidence = sum(confidences) / len(confidences) / 100 if confidences else 0
```

O con un servicio cloud (Google Vision, AWS Textract, Azure Form Recognizer):

```python
from google.cloud import vision
client = vision.ImageAnnotatorClient()
response = client.text_detection(image=vision.Image(content=image_bytes))
ocr_text = response.full_text_annotation.text
```
