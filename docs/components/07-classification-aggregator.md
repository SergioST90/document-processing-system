# Classification Aggregator

## Que hace

Es el punto de **fan-in** del pipeline tras la clasificacion. Recoge los resultados de clasificacion de todas las paginas de un request (tanto las automaticas como las corregidas manualmente por el back office) y, cuando todas las paginas han sido clasificadas, las agrupa en documentos logicos.

Es el componente mas complejo arquitectonicamente porque:
1. Debe manejar concurrencia (multiples paginas del mismo request llegan en paralelo).
2. Debe detectar cuando tiene TODAS las paginas para poder agrupar.
3. Tras agrupar, genera un nuevo fan-out: un mensaje por documento para la fase de extraccion.

**Fichero**: `src/components/classification_aggregator/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa cuando un mensaje llega a la cola `q.classification_aggregator` (routing key `page.classified`), publicado por:
- El **Classifier** (paginas clasificadas automaticamente con alta confianza)
- El **Back Office** (paginas clasificadas manualmente por un operador)

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=classification_aggregator
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.classification_aggregator`.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Incremento atomico del contador**: Ejecuta directamente SQL contra la tabla `aggregation_state`:

   ```sql
   UPDATE aggregation_state
   SET received_count = received_count + 1, updated_at = NOW()
   WHERE request_id = :request_id AND stage = 'classification'
   RETURNING received_count, expected_count
   ```

   Esto es **atomico a nivel de fila** en PostgreSQL. Si 5 paginas llegan simultaneamente y 5 replicas del aggregator las procesan a la vez, cada `UPDATE` incrementa correctamente gracias al row-level locking. El `RETURNING` devuelve el nuevo valor despues del incremento.

2. **Comprobacion de completitud**:
   - Si `received_count < expected_count`: devuelve lista vacia (no hace nada, sigue esperando mas paginas).
   - Si `received_count == expected_count`: todas las paginas han llegado.

3. **Marca como completo**: Actualiza `is_complete = true` en `aggregation_state`.

4. **Carga de paginas**: Lee todas las filas `Page` del request, ordenadas por `page_index`.

5. **Agrupacion en documentos**: Llama a `_group_pages_into_documents()` que agrupa paginas consecutivas del mismo `doc_type`. Por ejemplo:

   ```
   Pagina 0: invoice  ]
   Pagina 1: invoice  ] -> Documento A (invoice, paginas [0,1])
   Pagina 2: id_card  ] -> Documento B (id_card, paginas [2])
   Pagina 3: payslip  ]
   Pagina 4: payslip  ] -> Documento C (payslip, paginas [3,4])
   ```

6. **Creacion de registros**:
   - Actualiza `Request.document_count` y `Request.status` a `"extracting"`
   - Crea un `AggregationState` para la fase de extraccion con `expected_count = document_count`
   - Para cada grupo: crea una fila `Document` y actualiza las paginas con el `document_id`

7. **Fan-out de documentos**: Para cada documento creado, publica un mensaje con:
   - `document_id`: UUID del documento
   - `document_count`: total de documentos
   - `payload`: contiene `doc_type`, `page_indices`, y un diccionario con los textos OCR de todas las paginas del documento

### Mensajes de entrada y salida

**Entrada** (de `q.classification_aggregator`, llegan N mensajes, uno por pagina):
```json
{
  "request_id": "uuid",
  "page_index": 2,
  "page_count": 5,
  "payload": {
    "doc_type": "invoice",
    "classification_confidence": 0.92
  }
}
```

**Salida** (a `q.extractor` via routing key `doc.extract`, M mensajes, uno por documento):
```json
{
  "request_id": "uuid",
  "document_id": "uuid-documento",
  "document_count": 3,
  "source_component": "classification_aggregator",
  "payload": {
    "doc_type": "invoice",
    "page_indices": [0, 1],
    "document_id": "uuid-documento",
    "ocr_texts": {
      "0": "FACTURA\nNumero: F-2024-00142\n...",
      "1": "Lineas de detalle:\n..."
    }
  }
}
```

### Logica de agrupacion

El metodo `_group_pages_into_documents()` agrupa paginas **consecutivas** del mismo tipo. Esto significa que si las paginas 0 y 2 son ambas `invoice` pero la pagina 1 es `id_card`, se generan tres documentos distintos (no dos).

Este algoritmo asume que las paginas de un mismo documento estan juntas en el fichero original. Si se necesita una logica de agrupacion mas sofisticada (por ejemplo, basada en contenido o separadores), se sobreescribiria este metodo.

### Concurrencia y seguridad

El patron de conteo atomico con `UPDATE ... RETURNING` garantiza que:
- No hay race conditions aunque multiples replicas procesen paginas a la vez
- Exactamente un worker ejecutara la logica de agrupacion (el que reciba el ultimo `received_count == expected_count`)
- Si un worker falla antes de hacer commit, la transaccion hace rollback y el mensaje se reencola (requeue)
