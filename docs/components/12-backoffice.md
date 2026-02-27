# Back Office

## Que hace

Es la interfaz de intervencion humana (human-in-the-loop) del sistema. Proporciona una aplicacion web donde los operadores del back office pueden:

1. Ver la cola de tareas pendientes (clasificaciones y extracciones que requieren revision manual).
2. Reclamar una tarea para trabajar en ella.
3. Revisar los datos automaticos (texto OCR, tipo sugerido, datos extraidos) y corregirlos.
4. Enviar la correccion, que se reinyecta en el pipeline para continuar el procesamiento.

Tambien expone una API programatica para integracion con sistemas externos.

**Fichero**: `src/components/backoffice/app.py`

## Como se utiliza

### Acceder al dashboard

Abrir en el navegador:
```
http://localhost:8001/?operator=nombre_operador
```

El parametro `operator` identifica al operador (en esta version MVP no hay autenticacion; en produccion se integraria con un sistema de login).

### Flujo del operador

1. El operador abre el dashboard y ve la lista de tareas pendientes.
2. Hace clic en **"Reclamar"** en una tarea pendiente. La tarea pasa a estado `assigned` y queda reservada para ese operador.
3. Hace clic en **"Resolver"** para abrir la pagina de detalle.
4. Revisa los datos:
   - **Tarea de clasificacion**: ve el texto OCR y el tipo sugerido por el clasificador automatico. Selecciona el tipo correcto de un dropdown.
   - **Tarea de extraccion**: ve los datos parcialmente extraidos y los textos OCR. Edita el JSON con los campos corregidos o completados.
5. Hace clic en **"Enviar correccion"**. La tarea se marca como completada y el resultado se publica de vuelta en el pipeline.

### API programatica

Listar tareas pendientes:
```bash
curl http://localhost:8001/api/tasks?status=pending
curl http://localhost:8001/api/tasks?status=pending&skill=classification
```

Respuesta:
```json
[
  {
    "id": "uuid",
    "request_id": "uuid",
    "task_type": "classification",
    "status": "pending",
    "priority": 3,
    "assigned_to": null,
    "created_at": "2024-01-15T10:30:05Z",
    "input_data": {
      "page_index": 2,
      "ocr_text": "FACTURA\n...",
      "suggested_type": "invoice",
      "confidence": 0.65
    }
  }
]
```

Reclamar una tarea:
```bash
curl -X POST http://localhost:8001/tasks/{task_id}/claim \
  -F operator=juan
```

Enviar correccion de clasificacion:
```bash
curl -X POST http://localhost:8001/tasks/{task_id}/submit \
  -F operator=juan \
  -F doc_type=invoice
```

Enviar correccion de extraccion:
```bash
curl -X POST http://localhost:8001/tasks/{task_id}/submit \
  -F operator=juan \
  -F extracted_data='{"invoice_number": "F-2024-00142", "total_amount": 1250.00}'
```

### Variable de entorno

```bash
DOCPROC_RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
DOCPROC_DATABASE_URL=postgresql+asyncpg://docproc:docproc@postgres:5432/docproc
```

## Como esta implementado

### Tipo de componente

**No** hereda de `BaseComponent`. Es una aplicacion FastAPI independiente que se ejecuta con uvicorn en el puerto 8001. Combina endpoints HTML (para el dashboard del operador) y endpoints API JSON (para integracion programatica).

### Ciclo de vida (lifespan)

Al arrancar:
1. Configura logging estructurado
2. Crea engine de BD y session factory
3. Conecta a RabbitMQ y declara la topologia (necesita publicar mensajes de vuelta al pipeline)
4. Inicializa un `WorkflowLoader` para resolver dinamicamente a que etapa reinyectar las tareas completadas

### Endpoints HTML

**`GET /`** - Dashboard principal:
- Consulta la tabla `backoffice_tasks` filtrando por `status IN ('pending', 'assigned')`
- Ordena por prioridad (menor = mas urgente) y fecha de creacion
- Renderiza `templates/index.html` con Jinja2
- Muestra estadisticas: tareas pendientes, asignadas, por tipo
- Auto-refresh cada 10 segundos via JavaScript

**`GET /tasks/{task_id}`** - Detalle de tarea:
- Carga la tarea de BD
- Renderiza `templates/task.html` con Jinja2
- Para clasificacion: muestra texto OCR, tipo sugerido, confianza, y un dropdown para seleccionar el tipo correcto
- Para extraccion: muestra datos extraidos parciales, textos OCR, y un textarea para editar el JSON

### Endpoints de accion

**`POST /tasks/{task_id}/claim`**:
- Verifica que la tarea esta en estado `pending`
- La pasa a `assigned` con el nombre del operador y timestamp
- Redirige al dashboard

**`POST /tasks/{task_id}/submit`**:
- Marca la tarea como `completed`
- **Resolucion dinamica de la siguiente etapa**: Lee `task.source_stage` y `task.workflow_name` (almacenados por el componente que derivo al backoffice) y usa el `WorkflowLoader` para obtener la siguiente etapa del workflow via `get_next_stage()`. Si la tarea es legacy (sin estos campos), usa fallback hardcodeado por compatibilidad.
- Segun el `task_type`:

  **Clasificacion:**
  1. Lee el `doc_type` del formulario (o usa el sugerido si no se cambio)
  2. Actualiza la fila `Page`: `doc_type = tipo_corregido`, `classification_confidence = 1.0`, `status = "classified"`
  3. Publica un `PipelineMessage` al exchange `doc.direct` con el routing key y `current_stage` resueltos dinamicamente del workflow (por defecto la siguiente etapa tras `classify` es `classification_aggregation` con routing key `page.classified`)
  4. Este mensaje llega al aggregator correspondiente, que incrementa su contador normalmente

  **Extraccion:**
  1. Parsea el JSON de `extracted_data` del formulario
  2. Actualiza la fila `Document`: merge del `extracted_data` existente con las correcciones, `extraction_confidence = 1.0`, `status = "extracted"`
  3. Publica un `PipelineMessage` al exchange `doc.direct` con el routing key y `current_stage` resueltos dinamicamente del workflow (por defecto la siguiente etapa tras `extract` es `extraction_aggregation` con routing key `doc.extracted`)
  4. Este mensaje llega al aggregator correspondiente, que incrementa su contador normalmente

### Reinyeccion dinamica en el pipeline

El punto clave del diseno es que el Back Office **no es un callejon sin salida**. Cuando un operador completa una tarea, el resultado se reinyecta en el pipeline consultando el workflow YAML:

1. Se lee `source_stage` y `workflow_name` de la `BackofficeTask` (estos campos fueron almacenados por el componente que derivo al backoffice, ej: el Classifier almacena `source_stage="classify"`, `workflow_name="default"`).
2. Se llama a `WorkflowLoader.get_next_stage(workflow_name, source_stage)` para obtener la siguiente etapa.
3. Se publica al `routing_key` de esa etapa y se establece `current_stage` en el mensaje.

Ejemplo con el workflow default:
```
Clasificacion manual completada
  --> source_stage="classify", workflow_name="default"
  --> get_next_stage("default", "classify") = etapa "classification_aggregation" (routing_key="page.classified")
  --> publica a "page.classified" --> llega a q.classification_aggregator

Extraccion manual completada
  --> source_stage="extract", workflow_name="default"
  --> get_next_stage("default", "extract") = etapa "extraction_aggregation" (routing_key="doc.extracted")
  --> publica a "doc.extracted" --> llega a q.extraction_aggregator
```

Con un workflow distinto, el destino seria la etapa que corresponda segun la definicion YAML. Los aggregators no distinguen si el mensaje viene del componente automatico o del back office. Solo cuentan mensajes recibidos contra el total esperado.

Para tareas legacy (sin `source_stage`/`workflow_name`), se mantiene un fallback hardcodeado a los routing keys por defecto.

### Interfaz web

La interfaz usa HTML/CSS inline en los templates Jinja2 (sin framework frontend). Incluye:

- **Dashboard** (`templates/index.html`):
  - Cabecera con nombre del operador
  - 4 tarjetas de estadisticas (pendientes, asignadas, clasificaciones, extracciones)
  - Tabla con todas las tareas: ID, tipo, estado, prioridad, request, timestamp, acciones
  - Botones "Reclamar" y "Resolver" segun el estado

- **Detalle de tarea** (`templates/task.html`):
  - Informacion de la tarea (tipo, prioridad, request ID, estado)
  - Datos de entrada: texto OCR, tipo sugerido/datos extraidos
  - Formulario de correccion adaptado al tipo de tarea
  - Botones "Enviar correccion" y "Cancelar"

### Endpoint API

**`GET /api/tasks`**: Lista tareas con filtros opcionales:
- `status`: filtrar por estado (default: `"pending"`)
- `skill`: filtrar por skill requerido (usa `ARRAY @> ARRAY` de PostgreSQL)

Devuelve JSON para integracion con sistemas externos o una futura UI mas sofisticada.

### Extensiones futuras

- **Autenticacion**: Integrar con OAuth2/LDAP para identificar operadores reales.
- **Asignacion automatica**: En vez de que el operador reclame tareas manualmente, asignarlas automaticamente segun skills, carga y disponibilidad.
- **WebSocket**: Notificaciones push en tiempo real cuando llegan nuevas tareas, en vez del auto-refresh de 10 segundos.
- **Heartbeat de operador**: Detectar si un operador esta inactivo y reasignar sus tareas.
- **Metricas de operador**: Tiempo medio de resolucion, tasa de correccion, productividad.
