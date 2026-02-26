# API Gateway

## Que hace

Es el punto de entrada HTTP del sistema. Recibe peticiones de clientes con ficheros y metadatos, crea el registro del trabajo en la base de datos, almacena el fichero en disco y publica el primer mensaje en el pipeline de RabbitMQ para iniciar el procesamiento.

Tambien expone un endpoint para consultar el estado de un trabajo en cualquier momento.

**Fichero**: `src/components/api_gateway/app.py`

## Como se utiliza

### Enviar un documento a procesar

```bash
curl -X POST http://localhost:8000/process \
  -F file=@documento.pdf \
  -F metadata='{"client_id": "acme", "project": "facturas"}' \
  -F channel=api \
  -F workflow=default \
  -F external_id=REF-2024-001
```

**Parametros del formulario:**

| Parametro | Tipo | Obligatorio | Descripcion |
|---|---|---|---|
| `file` | File | Si | Fichero a procesar (PDF, TIFF, ZIP, etc.) |
| `metadata` | JSON string | No | Metadatos arbitrarios del cliente (default: `{}`) |
| `channel` | string | No | Canal de entrada (default: `"api"`) |
| `workflow` | string | No | Nombre del flujo a ejecutar (default: `"default"`) |
| `external_id` | string | No | Referencia externa del cliente |

**Respuesta (201):**

```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "received"
}
```

### Consultar el estado de un trabajo

```bash
curl http://localhost:8000/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Respuesta:**

```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "workflow_name": "default",
  "created_at": "2024-01-15T10:30:00Z",
  "deadline_utc": "2024-01-15T10:31:00Z",
  "completed_at": "2024-01-15T10:30:28Z",
  "page_count": 5,
  "document_count": 3,
  "result": {
    "documents": [
      {"doc_type": "invoice", "extracted_data": {...}},
      {"doc_type": "id_card", "extracted_data": {...}}
    ]
  },
  "error": null
}
```

**Posibles valores de `status`:**
`received` | `routing` | `splitting` | `processing` | `extracting` | `consolidating` | `completed` | `failed` | `sla_breached`

### Health check

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

## Como esta implementado

### Tipo de componente

**No** hereda de `BaseComponent`. Es una aplicacion FastAPI independiente que se ejecuta con uvicorn. La razon es que necesita exponer endpoints HTTP en vez de consumir de una cola.

### Ciclo de vida (lifespan)

Al arrancar (`lifespan` context manager):
1. Inicializa el logging estructurado con structlog
2. Crea el engine de SQLAlchemy async y la session factory, almacenandolos en `app.state`
3. Conecta a RabbitMQ, abre un canal y declara toda la topologia de exchanges/colas
4. Crea el directorio de almacenamiento de ficheros si no existe

Al apagar:
1. Cierra la conexion RabbitMQ
2. Dispone el engine de BD

### POST /process - Flujo interno

1. **Validacion**: Parsea el campo `metadata` como JSON. Devuelve 400 si no es JSON valido.

2. **Almacenamiento del fichero**: Crea un directorio unico por request (`{storage_path}/{request_id}/`) y guarda el fichero con su nombre original. El contenido se lee completo en memoria con `await file.read()` y se escribe en disco con `file_path.write_bytes()`.

3. **Registro en BD**: Crea una fila en la tabla `requests` con:
   - UUID generado
   - Canal, workflow, nombre original del fichero, ruta de almacenamiento
   - Metadatos del cliente en campo JSONB
   - Status inicial: `"received"`
   - Timestamps de creacion

4. **Publicacion del mensaje**: Construye un `PipelineMessage` con:
   - `request_id`: el UUID generado
   - `workflow_name`: del parametro del formulario
   - `payload`: contiene channel, file_path, original_filename y metadata
   - `source_component`: `"api_gateway"`

   Publica al exchange `doc.direct` con routing key `"request.new"`, que enruta el mensaje a la cola `q.workflow_router`.

5. **Respuesta**: Devuelve inmediatamente el `request_id` y status `"received"`. El procesamiento continua de forma asincrona.

### GET /status/{request_id} - Flujo interno

1. Ejecuta un `SELECT` a la tabla `requests` por UUID.
2. Si no existe, devuelve 404.
3. Mapea los campos de la fila a `JobStatusResponse`, incluyendo el `result_payload` completo si el trabajo ya finalizo.

### Configuracion Docker

Se ejecuta como un servicio separado en docker-compose:

```yaml
api-gateway:
  command: uvicorn src.components.api_gateway.app:app --host 0.0.0.0 --port 8000
  ports:
    - "8000:8000"
```

Necesita acceso al volumen de almacenamiento compartido (`file_storage`) para que el Splitter pueda leer los ficheros que el Gateway guarda.
