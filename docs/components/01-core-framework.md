# Core Framework

## Que hace

El core framework es la base compartida por todos los componentes del pipeline. Proporciona:

- **BaseComponent**: clase abstracta de la que heredan todos los componentes del pipeline (excepto el API Gateway, el Back Office y el SLA Monitor que son servicios HTTP o procesos especiales).
- **PipelineMessage**: schema Pydantic que define el envelope universal de mensajes que viaja por todas las colas de RabbitMQ.
- **Topologia RabbitMQ**: declaracion centralizada de todos los exchanges, colas y bindings del sistema.
- **Modelos ORM**: las 6 tablas de PostgreSQL que almacenan el estado de todo el sistema.
- **Utilidades**: carga de workflows YAML, calculo de SLA, health checks HTTP, configuracion centralizada.

## Como se utiliza

### Crear un nuevo componente del pipeline

Para crear un nuevo componente, se hereda de `BaseComponent` y se implementan dos cosas obligatorias: `component_name` y `process_message`.

```python
from src.core.base_component import BaseComponent
from src.core.schemas import PipelineMessage
from sqlalchemy.ext.asyncio import AsyncSession


class MiComponente(BaseComponent):

    component_name = "mi_componente"  # Debe ser unico. La cola sera q.mi_componente

    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        # Logica de negocio aqui
        # session ya tiene una transaccion abierta (auto-commit si no hay excepcion)

        # Devolver lista de (routing_key, mensaje) para publicar al siguiente paso
        out = message.model_copy(update={"source_component": self.component_name})
        return [("siguiente.routing.key", out)]
```

### Metodos opcionales para sobreescribir

```python
class MiComponente(BaseComponent):
    component_name = "mi_componente"

    @property
    def input_queue(self) -> str:
        """Cambiar la cola de entrada si no sigue la convencion q.{component_name}."""
        return "q.cola_personalizada"

    async def setup(self) -> None:
        """Inicializacion que se ejecuta una sola vez al arrancar (cargar modelos ML, etc)."""
        self.model = await load_ml_model()

    async def teardown(self) -> None:
        """Limpieza al apagar."""
        await super().teardown()  # Importante: llama al padre para cerrar conexiones
```

### Publicar al back office

Desde cualquier componente, se puede enviar un mensaje al exchange de back office:

```python
await self.publish_to_backoffice("task.classification", mensaje)
```

### Registrar el componente

Anadir la entrada al diccionario `COMPONENT_REGISTRY` en `src/__main__.py`:

```python
COMPONENT_REGISTRY = {
    ...
    "mi_componente": "src.components.mi_componente.component.MiComponente",
}
```

### PipelineMessage

El mensaje universal lleva estos campos:

| Campo | Tipo | Descripcion |
|---|---|---|
| `request_id` | UUID | Identificador unico de la peticion del cliente |
| `trace_id` | UUID | ID de traza para correlacionar logs |
| `workflow_name` | str | Nombre del flujo (ej: "default") |
| `deadline_utc` | datetime? | Deadline absoluto del SLA |
| `page_index` | int? | Indice de pagina (en etapas page-level) |
| `page_count` | int? | Total de paginas del request |
| `document_id` | UUID? | ID del documento logico (en etapas document-level) |
| `document_count` | int? | Total de documentos del request |
| `payload` | dict | Datos flexibles especificos de cada etapa |
| `source_component` | str? | Nombre del componente que genero este mensaje |
| `created_at` | datetime | Timestamp de creacion del mensaje |

## Como esta implementado

### BaseComponent (`src/core/base_component.py`)

Es una clase abstracta (ABC) que gestiona todo el ciclo de vida:

1. **`__init__`**: Inicializa el logger (structlog), el engine de BD (SQLAlchemy async), la session factory, el health server HTTP y el evento de shutdown.

2. **`run()`**: Metodo principal. Ejecuta en secuencia:
   - Registra signal handlers para SIGTERM/SIGINT (graceful shutdown)
   - Arranca el health server HTTP en el puerto configurado
   - Llama a `setup()` (hook para inicializacion personalizada)
   - Conecta a RabbitMQ con `aio_pika.connect_robust` (reconexion automatica)
   - Configura QoS con `prefetch_count` (por defecto 1, fair dispatch)
   - Declara toda la topologia de exchanges/colas (idempotente)
   - Comienza a consumir de `self.input_queue`
   - Espera al evento de shutdown

3. **`_on_message()`**: Callback para cada mensaje recibido:
   - Deserializa el body JSON a `PipelineMessage` con Pydantic
   - Abre una sesion de BD con transaccion (`async with session.begin()`)
   - Llama a `process_message()` (logica del hijo)
   - Si `process_message` no lanza excepcion: hace commit de la transaccion
   - Publica todos los mensajes de salida al exchange `doc.direct`
   - Hace ACK del mensaje RabbitMQ (via `raw_message.process(requeue=True)`)
   - Si hay excepcion: rollback de BD + NACK con requeue

4. **`_publish()`**: Publica un mensaje a un exchange con delivery mode PERSISTENT (sobrevive restart del broker). Incluye headers con `request_id` y `component` para trazabilidad.

5. **`teardown()`**: Cierra la conexion RabbitMQ, dispone el engine de BD y para el health server.

### Topologia RabbitMQ (`src/core/rabbitmq.py`)

Define 3 exchanges y 11 colas de forma declarativa:

**Exchanges:**
- `doc.direct` (DIRECT): pipeline principal. Cada cola se bindea a un routing key especifico.
- `doc.backoffice` (DIRECT): tareas para operadores humanos.
- `doc.dlx` (FANOUT): dead letter exchange. Recibe mensajes que fallan o expiran.

**Colas y bindings:**

| Cola | Exchange | Routing Key |
|---|---|---|
| `q.workflow_router` | doc.direct | `request.new` |
| `q.splitter` | doc.direct | `request.split` |
| `q.ocr` | doc.direct | `page.ocr` |
| `q.classifier` | doc.direct | `page.classify` |
| `q.classification_aggregator` | doc.direct | `page.classified` |
| `q.extractor` | doc.direct | `doc.extract` |
| `q.extraction_aggregator` | doc.direct | `doc.extracted` |
| `q.consolidator` | doc.direct | `request.consolidate` |
| `q.backoffice.classification` | doc.backoffice | `task.classification` |
| `q.backoffice.extraction` | doc.backoffice | `task.extraction` |
| `q.dead_letters` | doc.dlx | (all) |

Cada cola (excepto dead letters) se configura con:
- `x-dead-letter-exchange: doc.dlx` (mensajes fallidos van a DLQ)
- `x-message-ttl: 300000` (5 minutos de vida maxima)
- `durable: true` (sobrevive restart del broker)

La funcion `setup_rabbitmq_topology()` es idempotente: se puede llamar multiples veces sin efecto.

### Modelos ORM (`src/core/models.py`)

6 tablas con SQLAlchemy 2.0 mapped columns:

| Tabla | Descripcion | Indices |
|---|---|---|
| `requests` | Tracking central de cada peticion | Por status, por deadline (parcial) |
| `pages` | Una fila por pagina extraida | Por request_id, por document_id |
| `documents` | Documentos logicos (agrupaciones de paginas) | Por request_id |
| `backoffice_tasks` | Tareas de intervencion humana | Por (status, priority), por assigned_to (parcial) |
| `operators` | Registro de operadores | Por username (unique) |
| `aggregation_state` | Estado de fan-in de los aggregators | Por (request_id, stage) unique |

La tabla `aggregation_state` es clave: los aggregators la usan para conteo atomico con `UPDATE ... SET received_count = received_count + 1 RETURNING`. Esto permite que multiples replicas del aggregator procesen mensajes concurrentemente sin condiciones de carrera.

### Configuracion (`config/settings.py`)

Usa `pydantic-settings` con prefijo `DOCPROC_` para variables de entorno. Todos los parametros son configurables sin tocar codigo:

```bash
DOCPROC_COMPONENT_NAME=ocr
DOCPROC_RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/
DOCPROC_DATABASE_URL=postgresql+asyncpg://docproc:docproc@postgres:5432/docproc
DOCPROC_CLASSIFICATION_CONFIDENCE_THRESHOLD=0.85
```

### Workflow Loader (`src/core/workflow_loader.py`)

Carga ficheros YAML de `config/workflows/` y los parsea a modelos Pydantic (`WorkflowConfig`, `StageConfig`, `SLAConfig`). Cachea en memoria para no releer el fichero en cada peticion.

### Health Server (`src/core/health.py`)

Servidor HTTP ligero con aiohttp que expone:
- `GET /health`: siempre devuelve 200 (liveness)
- `GET /ready`: devuelve 200 solo cuando el componente esta consumiendo mensajes, 503 si no (readiness)

Se usa para los probes de Kubernetes.
