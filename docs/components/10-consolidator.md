# Consolidator

## Que hace

Es la etapa terminal del pipeline. Carga todos los documentos extraidos de un request, ensambla el resultado final en un JSON estructurado, lo almacena en la base de datos y marca el trabajo como completado.

Despues de este componente, el cliente puede obtener el resultado consultando `GET /status/{request_id}`.

**Fichero**: `src/components/consolidator/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.consolidator` (routing key `request.consolidate`), publicado por el Extraction Aggregator.

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=consolidator
```

### Obtener el resultado

Una vez consolidado, el resultado esta disponible:

```bash
curl http://localhost:8000/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

```json
{
  "status": "completed",
  "result": {
    "request_id": "a1b2c3d4-...",
    "workflow": "default",
    "total_pages": 5,
    "total_documents": 3,
    "documents": [
      {
        "document_id": "uuid-doc-1",
        "doc_type": "invoice",
        "page_indices": [0, 1],
        "extracted_data": {
          "invoice_number": "F-2024-00142",
          "total_amount": 1250.00,
          "vendor_name": "Empresa ABC S.L.",
          "date": "2024-01-15"
        },
        "extraction_confidence": 0.92,
        "status": "completed"
      },
      {
        "document_id": "uuid-doc-2",
        "doc_type": "id_card",
        "page_indices": [2],
        "extracted_data": {
          "full_name": "Juan Garcia Lopez",
          "id_number": "12345678Z",
          "date_of_birth": "1985-03-15"
        },
        "extraction_confidence": 0.98,
        "status": "completed"
      }
    ]
  }
}
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.consolidator`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Carga de datos**: Lee la fila `Request` y todas sus filas `Document` asociadas de la BD, ordenadas por fecha de creacion.

2. **Ensamblaje del resultado**: Construye un diccionario `result_payload` con:
   - `request_id`: UUID del request
   - `workflow`: nombre del workflow ejecutado
   - `total_pages`: numero total de paginas del fichero original
   - `total_documents`: numero de documentos logicos identificados
   - `documents`: lista con los datos de cada documento:
     - `document_id`, `doc_type`, `page_indices`
     - `extracted_data`: datos estructurados extraidos
     - `extraction_confidence`: confianza de la extraccion
     - `status`: se actualiza a `"completed"`

3. **Actualizacion en BD**:
   - Cada documento: `status = "completed"`
   - Request: `result_payload = result_payload` (el JSON ensamblado)
   - Request: `status = "completed"`
   - Request: `completed_at = datetime.now(UTC)`

4. **Sin mensajes de salida**: Devuelve lista vacia (`[]`). Es la etapa terminal del pipeline. Al ser terminal, el framework de enrutamiento dinamico no intenta resolver ninguna etapa siguiente.

### Mensajes de entrada y salida

**Entrada** (de `q.consolidator`):
```json
{
  "request_id": "uuid",
  "source_component": "extraction_aggregator"
}
```

**Salida**: Ninguna. El Consolidator no publica mensajes. El resultado se consulta via la API `GET /status/{request_id}`.

### Extensiones futuras

El Consolidator es el punto natural para anadir:

- **Webhooks**: Enviar el resultado a una URL del cliente cuando se complete.
- **Callback a sistema externo**: Publicar a otra cola o API para que otro sistema consuma el resultado.
- **Generacion de respuesta formateada**: Transformar el resultado a XML, CSV, o el formato que espere el sistema del cliente.
- **Respuesta parcial temprana**: Si el SLA esta en riesgo y ya se tienen algunos documentos completos, el Consolidator podria emitir una respuesta parcial sin esperar al Extraction Aggregator.
