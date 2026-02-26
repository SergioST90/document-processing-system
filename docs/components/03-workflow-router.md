# Workflow Router

## Que hace

Es el primer componente del pipeline tras la recepcion. Lee los metadatos del trabajo, determina que flujo de trabajo (workflow) debe ejecutarse, carga la configuracion de ese flujo desde el YAML correspondiente, calcula el deadline del SLA y reenvía el mensaje al siguiente paso del pipeline (el Splitter).

**Fichero**: `src/components/workflow_router/component.py`

## Como se utiliza

### En el pipeline

No se invoca directamente. Se activa automaticamente cuando un mensaje llega a la cola `q.workflow_router` (routing key `request.new`), publicado por el API Gateway.

### Configuracion de flujos

Los flujos se definen en `config/workflows/` como ficheros YAML. El nombre del fichero debe coincidir con el campo `workflow_name` del mensaje. Por ejemplo, si el mensaje tiene `workflow_name: "default"`, se carga `config/workflows/default.yaml`.

Para crear un flujo nuevo:

1. Crear `config/workflows/mi_flujo.yaml`:
```yaml
name: mi_flujo
description: "Flujo personalizado para facturas rapidas"
version: 1
sla:
  deadline_seconds: 30
  warn_threshold_pct: 70
  escalation_threshold_pct: 90
stages:
  - name: split
    component: splitter
    routing_key: request.split
    timeout_seconds: 5
  # ... (mas etapas)
```

2. Al enviar una peticion, especificar el workflow:
```bash
curl -X POST http://localhost:8000/process \
  -F file=@factura.pdf \
  -F workflow=mi_flujo
```

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=workflow_router
DOCPROC_WORKFLOWS_DIR=config/workflows   # Directorio donde busca los YAML
```

## Como esta implementado

### Tipo de componente

Hereda de `BaseComponent`. Consume de la cola `q.workflow_router`.

### Metodo `setup()`

Instancia el `WorkflowLoader` apuntando al directorio configurado en `settings.workflows_dir`. El loader cachea los YAML parseados en memoria tras la primera carga.

### Metodo `process_message()`

Flujo interno paso a paso:

1. **Carga del workflow**: Llama a `self._workflow_loader.load(message.workflow_name)`. Esto lee y parsea el fichero YAML correspondiente (o lo obtiene de cache si ya se leyo). Si el fichero no existe, lanza `FileNotFoundError`.

2. **Calculo del deadline**: Llama a `calculate_deadline(workflow.sla.deadline_seconds)` que calcula `datetime.now(UTC) + timedelta(seconds=sla)`. Para un SLA de 60 segundos, el deadline es 60 segundos desde ahora.

3. **Actualizacion en BD**: Busca la fila `Request` por `request_id` y actualiza:
   - `status`: de `"received"` a `"routing"`
   - `deadline_utc`: el deadline calculado
   - `sla_seconds`: duracion del SLA en segundos
   - `updated_at`: timestamp actual

4. **Publicacion**: Crea una copia del mensaje con el `deadline_utc` anadido y `source_component: "workflow_router"`. Lo publica con routing key `"request.split"`, que lo envia a la cola `q.splitter`.

### Mensajes de entrada y salida

**Entrada** (de `q.workflow_router`):
```json
{
  "request_id": "uuid",
  "workflow_name": "default",
  "payload": {
    "channel": "api",
    "file_path": "/data/storage/uuid/documento.pdf",
    "original_filename": "documento.pdf",
    "metadata": {...}
  }
}
```

**Salida** (a `q.splitter` via routing key `request.split`):
```json
{
  "request_id": "uuid",
  "workflow_name": "default",
  "deadline_utc": "2024-01-15T10:31:00Z",
  "source_component": "workflow_router",
  "payload": {
    "channel": "api",
    "file_path": "/data/storage/uuid/documento.pdf",
    "original_filename": "documento.pdf",
    "metadata": {...}
  }
}
```

La unica diferencia es que el mensaje de salida ya tiene `deadline_utc` establecido. A partir de aqui, todos los componentes downstream tienen visibilidad del deadline.
