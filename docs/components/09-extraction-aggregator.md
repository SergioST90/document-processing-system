# Extraction Aggregator

## Que hace

Es el segundo punto de **fan-in** del pipeline. Recoge los resultados de extraccion de todos los documentos de un request (tanto los automaticos como los corregidos por operadores) y, cuando todos los documentos han sido procesados, envía el trabajo al Consolidator para ensamblar la respuesta final.

Funciona con el mismo patron de conteo atomico que el Classification Aggregator, pero sobre la fase `"extraction"`.

**Fichero**: `src/components/extraction_aggregator/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.extraction_aggregator` (routing key `doc.extracted`), publicado por:
- El **Extractor** (documentos extraidos automaticamente con alta confianza)
- El **Back Office** (documentos completados manualmente por un operador)

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=extraction_aggregator
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.extraction_aggregator`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Incremento atomico del contador**: Ejecuta el mismo patron SQL que el Classification Aggregator:

   ```sql
   UPDATE aggregation_state
   SET received_count = received_count + 1, updated_at = NOW()
   WHERE request_id = :request_id AND stage = 'extraction'
   RETURNING received_count, expected_count
   ```

2. **Comprobacion de completitud**:
   - Si `received_count < expected_count`: devuelve lista vacia. Sigue esperando mas documentos.
   - Si `received_count == expected_count`: todos los documentos han sido extraidos.

3. **Marca como completo**: Actualiza `is_complete = true` en `aggregation_state` para la fase de extraccion.

4. **Publicacion**: Devuelve `[("__next__", out_message)]`. El framework resuelve el sentinela `__next__` consultando el workflow YAML para obtener la siguiente etapa (por defecto `consolidate` con routing key `request.consolidate`, que va a la cola `q.consolidator`).

### Mensajes de entrada y salida

**Entrada** (de `q.extraction_aggregator`, llegan M mensajes, uno por documento):
```json
{
  "request_id": "uuid",
  "document_id": "uuid-doc",
  "document_count": 3,
  "payload": {
    "doc_type": "invoice",
    "extracted_data": {...},
    "extraction_confidence": 0.91
  }
}
```

**Salida** (sentinela `__next__`, resuelto por el framework a `q.consolidator` via `request.consolidate` en el workflow default, 1 solo mensaje):
```json
{
  "request_id": "uuid",
  "source_component": "extraction_aggregator"
}
```

El mensaje de salida no necesita llevar los datos de todos los documentos en el payload. El Consolidator los leera directamente de la base de datos, donde cada documento ya tiene su `extracted_data` almacenado.

### Diferencias con el Classification Aggregator

| Aspecto | Classification Aggregator | Extraction Aggregator |
|---|---|---|
| Stage en aggregation_state | `"classification"` | `"extraction"` |
| Que recibe | Paginas clasificadas | Documentos extraidos |
| Que hace al completar | Agrupa paginas en documentos + fan-out | Solo envía a consolidacion |
| Complejidad | Alta (agrupacion + creacion docs) | Baja (solo conteo y forward) |

El Extraction Aggregator es intencionalmente mas simple porque no necesita agrupar ni transformar datos: solo necesita esperar a que todos los documentos esten listos.
