# Splitter

## Que hace

Toma un fichero recibido del cliente (PDF, TIFF, ZIP, etc.), lo descompone en paginas individuales y genera un mensaje por cada pagina. Es el punto de **fan-out** principal del pipeline: un unico mensaje de entrada produce N mensajes de salida (uno por pagina).

Ademas, crea el registro de `aggregation_state` que los aggregators downstream usaran para saber cuando todas las paginas han sido procesadas.

**Fichero**: `src/components/splitter/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.splitter` (routing key `request.split`), publicado por el Workflow Router.

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=splitter
DOCPROC_STORAGE_PATH=/data/storage   # Donde estan almacenados los ficheros
```

### Escalar el splitter

Si hay muchos ficheros llegando simultaneamente, se pueden levantar multiples replicas. Cada replica consume de la misma cola `q.splitter` con prefetch 1 (fair dispatch).

```yaml
# docker-compose: escalar a 3 replicas
docker compose up --scale splitter=3
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.splitter`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Lectura del fichero**: Obtiene la ruta del fichero desde `message.payload["file_path"]`.

2. **Descompresion de paginas** (STUB): En la implementacion actual, simula la extraccion generando un numero aleatorio de paginas (3-5). En produccion, aqui se usaria PyPDF2 para PDFs, Pillow/pdf2image para imagenes, o zipfile para ZIPs.

3. **Actualizacion de la request**: Actualiza la fila `Request` en BD:
   - `page_count`: numero de paginas extraidas
   - `status`: `"splitting"`

4. **Creacion del aggregation_state**: Inserta una fila en `aggregation_state` con:
   - `request_id`: el ID del trabajo
   - `stage`: `"classification"` (identifica que este estado es para el aggregator de clasificacion)
   - `expected_count`: igual a `page_count`
   - `received_count`: 0

   Esta fila es critica: el Classification Aggregator la usara para saber cuando han llegado todas las paginas clasificadas.

5. **Creacion de paginas y fan-out**: Para cada pagina (de 0 a N-1):
   - Crea una fila `Page` en BD con `page_index`, `status: "extracted"` y la ruta del fichero
   - Crea un `PipelineMessage` con `page_index` y `page_count` establecidos
   - Lo anade a la lista de salida con routing key `"page.ocr"`

### Mensajes de entrada y salida

**Entrada** (1 mensaje de `q.splitter`):
```json
{
  "request_id": "uuid",
  "workflow_name": "default",
  "deadline_utc": "2024-01-15T10:31:00Z",
  "payload": {
    "file_path": "/data/storage/uuid/documento.pdf"
  }
}
```

**Salida** (N mensajes a `q.ocr` via routing key `page.ocr`):
```json
{
  "request_id": "uuid",
  "workflow_name": "default",
  "deadline_utc": "2024-01-15T10:31:00Z",
  "page_index": 0,
  "page_count": 5,
  "source_component": "splitter",
  "payload": {
    "file_path": "/data/storage/uuid/documento.pdf",
    "page_id": "uuid-de-la-pagina",
    "page_index": 0
  }
}
```

Se generan tantos mensajes como paginas tenga el fichero, cada uno con un `page_index` diferente. Todos van a la misma cola `q.ocr` y se procesan en paralelo por las N replicas del componente OCR.

### Para implementar el splitting real

Reemplazar la seccion STUB por logica real. Ejemplo con PyPDF2:

```python
from PyPDF2 import PdfReader
from pathlib import Path

reader = PdfReader(str(file_path))
page_count = len(reader.pages)

for i, pdf_page in enumerate(reader.pages):
    # Guardar cada pagina como fichero individual
    page_path = storage_dir / f"page_{i}.pdf"
    writer = PdfWriter()
    writer.add_page(pdf_page)
    with open(page_path, "wb") as f:
        writer.write(f)
```
