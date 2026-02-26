# DocProc - Sistema Distribuido de Procesamiento Documental

Sistema de orquestacion distribuida para el tratamiento automatizado de documentos. Recibe ficheros con documentos, los descompone en paginas, aplica OCR y clasificacion automatica, extrae datos estructurados y permite intervencion humana cuando la confianza automatica es insuficiente. Todo bajo gestion de SLA con deadlines configurables.

## Tabla de contenidos

- [Arquitectura general](#arquitectura-general)
- [Flujo de procesamiento](#flujo-de-procesamiento)
- [Stack tecnologico](#stack-tecnologico)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Inicio rapido](#inicio-rapido)
- [API Reference](#api-reference)
- [Componentes del pipeline](#componentes-del-pipeline)
- [Configuracion](#configuracion)
- [Definicion de workflows](#definicion-de-workflows)
- [Base de datos](#base-de-datos)
- [Topologia RabbitMQ](#topologia-rabbitmq)
- [Back Office - Interfaz de operadores](#back-office---interfaz-de-operadores)
- [Monitorizacion y SLA](#monitorizacion-y-sla)
- [Crear un nuevo componente](#crear-un-nuevo-componente)
- [Despliegue en Kubernetes](#despliegue-en-kubernetes)
- [Desarrollo](#desarrollo)
- [Documentacion de componentes](#documentacion-de-componentes)

---

## Arquitectura general

```
                          +------------------+
                          |    Cliente       |
                          |  (HTTP / SFTP)   |
                          +--------+---------+
                                   |
                          +--------v---------+
                          |   API Gateway    |  Puerto 8000
                          |   (FastAPI)      |
                          +--------+---------+
                                   |
                          pubblica a RabbitMQ
                                   |
     +-----------------------------v-----------------------------+
     |                    PIPELINE (RabbitMQ)                     |
     |                                                           |
     |  [Workflow Router] --> [Splitter] --> fan-out N paginas    |
     |       |                                |                  |
     |       v                                v                  |
     |  Selecciona flujo              [OCR] x N paginas          |
     |  Calcula SLA                         |                    |
     |                                      v                    |
     |                             [Classifier] x N              |
     |                              /            \               |
     |                     alta conf.         baja conf.         |
     |                         |                  |              |
     |                         v                  v              |
     |              [Classif. Aggregator]    [Back Office]        |
     |               (fan-in + agrupar)      (operador)          |
     |                         |                  |              |
     |                         +<----- page.classified           |
     |                         |                                 |
     |                    fan-out M docs                          |
     |                         |                                 |
     |                  [Extractor] x M                          |
     |                   /            \                          |
     |          alta conf.         baja conf.                    |
     |              |                  |                         |
     |              v                  v                         |
     |     [Extract. Aggregator]  [Back Office]                  |
     |          (fan-in)           (operador)                    |
     |              |                  |                         |
     |              +<----- doc.extracted                        |
     |              |                                            |
     |       [Consolidator]                                      |
     |       (resultado final)                                   |
     +-----------------------------------------------------------+
                                   |
                          +--------v---------+
                          |  SLA Monitor     |  (polling cada 5s)
                          |  (vigila plazos) |
                          +------------------+
```

**Principios de diseno:**

- **Monorepo con imagen Docker unica**: todos los componentes comparten el core framework. Una sola imagen Docker; la variable de entorno `DOCPROC_COMPONENT_NAME` determina que componente ejecutar.
- **Componentes reutilizables**: cada pieza hereda de `BaseComponent`, que gestiona la conexion a RabbitMQ, la sesion de BD, el logging estructurado, los health checks y el shutdown graceful.
- **Fan-out / Fan-in**: el Splitter genera N mensajes (uno por pagina) que se procesan en paralelo. Los Aggregators esperan a que lleguen todos los resultados usando conteo atomico en PostgreSQL.
- **Routing por confianza**: clasificacion y extraccion redirigen a operadores humanos cuando la confianza automatica es insuficiente.
- **Workflows declarativos**: los flujos se definen en YAML sin tocar codigo.

---

## Flujo de procesamiento

Un trabajo tipico sigue estos pasos:

1. **Recepcion**: el cliente envía un fichero + metadatos via `POST /process`. El API Gateway almacena el fichero, crea el registro en BD y publica el primer mensaje.

2. **Routing**: el Workflow Router lee los metadatos, selecciona el flujo YAML correspondiente y calcula el deadline del SLA.

3. **Descompresion** (fan-out): el Splitter extrae N paginas del fichero y genera un mensaje por pagina.

4. **OCR**: cada pagina se procesa en paralelo para extraer texto.

5. **Clasificacion**: cada pagina se clasifica por tipo documental (factura, DNI, nomina...).
   - Confianza alta: continua al aggregator.
   - Confianza baja: se crea una tarea en el back office para correccion manual.

6. **Agrupacion** (fan-in): cuando todas las paginas estan clasificadas, se agrupan en documentos logicos (paginas consecutivas del mismo tipo).

7. **Extraccion** (fan-out): cada documento se procesa para extraer campos estructurados segun su tipo.
   - Confianza alta: continua al aggregator.
   - Confianza baja: se crea una tarea en el back office.

8. **Consolidacion** (fan-in): cuando todos los documentos estan extraidos, se ensambla el resultado final.

9. **Resultado**: el cliente consulta `GET /status/{request_id}` y obtiene los datos estructurados.

---

## Stack tecnologico

| Capa | Tecnologia | Version |
|---|---|---|
| Lenguaje | Python | >= 3.11 |
| Framework HTTP | FastAPI + Uvicorn | 0.110+ |
| Colas de mensajes | RabbitMQ | 3.13 |
| Base de datos | PostgreSQL | 16 |
| ORM | SQLAlchemy (async) | 2.0+ |
| Migraciones | Alembic | 1.13+ |
| Cliente RabbitMQ | aio-pika | 9.4+ |
| Schemas | Pydantic v2 | 2.6+ |
| Configuracion | pydantic-settings | 2.1+ |
| Logging | structlog (JSON) | 24.1+ |
| Health checks | aiohttp | 3.9+ |
| Templates | Jinja2 | 3.1+ |
| Contenedores | Docker + Docker Compose | - |
| Orquestacion | Kubernetes + Kustomize | - |

---

## Estructura del proyecto

```
document-processing-system/
|
|-- pyproject.toml                          # Dependencias y build
|-- Dockerfile                              # Multi-stage, imagen unica
|-- docker-compose.yml                      # Entorno local completo (12 servicios)
|-- Makefile                                # Comandos de desarrollo
|-- alembic.ini                             # Configuracion de migraciones
|-- .env.example                            # Variables de entorno de referencia
|
|-- config/
|   |-- settings.py                         # Configuracion centralizada (pydantic-settings)
|   |-- logging.py                          # structlog JSON
|   |-- workflows/
|       |-- default.yaml                    # Workflow por defecto (7 etapas, 4 tipos doc.)
|
|-- src/
|   |-- __main__.py                         # Dispatcher: DOCPROC_COMPONENT_NAME -> componente
|   |
|   |-- core/                               # Framework compartido
|   |   |-- base_component.py               # Clase abstracta para componentes del pipeline
|   |   |-- schemas.py                      # PipelineMessage (envelope universal)
|   |   |-- models.py                       # 6 tablas ORM (requests, pages, documents, ...)
|   |   |-- rabbitmq.py                     # Topologia: 3 exchanges, 11 colas
|   |   |-- database.py                     # SQLAlchemy async engine/session
|   |   |-- workflow_loader.py              # Carga y cache de YAML
|   |   |-- sla.py                          # Utilidades de deadline
|   |   |-- health.py                       # HTTP health/ready server
|   |
|   |-- components/
|   |   |-- api_gateway/app.py              # FastAPI: POST /process, GET /status
|   |   |-- workflow_router/component.py    # Seleccion de flujo + calculo SLA
|   |   |-- splitter/component.py           # Descompresion + fan-out por pagina
|   |   |-- ocr/component.py               # Extraccion de texto (stub)
|   |   |-- classifier/component.py         # Clasificacion por tipo (stub)
|   |   |-- classification_aggregator/      # Fan-in + agrupacion en documentos
|   |   |-- extractor/component.py          # Extraccion de datos (stub)
|   |   |-- extraction_aggregator/          # Fan-in de documentos
|   |   |-- consolidator/component.py       # Ensamblaje del resultado final
|   |   |-- sla_monitor/component.py        # Polling de deadlines
|   |   |-- backoffice/                     # FastAPI + UI HTML para operadores
|   |       |-- app.py
|   |       |-- templates/index.html        # Dashboard del operador
|   |       |-- templates/task.html         # Detalle de tarea
|   |
|   |-- migrations/
|       |-- versions/001_initial_schema.py  # Schema completo (6 tablas)
|
|-- tests/                                  # Unit + integration tests
|-- k8s/                                    # Manifiestos Kubernetes (Kustomize)
|-- docs/components/                        # Documentacion detallada por componente
```

---

## Inicio rapido

### Requisitos previos

- Docker y Docker Compose
- (Opcional) Python 3.11+ para desarrollo local

### 1. Clonar y levantar

```bash
cd document-processing-system

# Construir y levantar todos los servicios (12 contenedores)
docker compose up --build -d
```

Esto levanta:
- **RabbitMQ** en `localhost:5672` (management UI en `localhost:15672`, user/pass: `guest/guest`)
- **PostgreSQL** en `localhost:5432` (user/pass/db: `docproc`)
- **API Gateway** en `localhost:8000`
- **Back Office** en `localhost:8001`
- 8 componentes del pipeline + SLA Monitor

### 2. Ejecutar migraciones

```bash
docker compose exec api-gateway alembic upgrade head
```

### 3. Enviar un documento

```bash
# Enviar cualquier fichero para pruebas (el OCR es stub)
curl -X POST http://localhost:8000/process \
  -F file=@cualquier_fichero.pdf \
  -F metadata='{"client": "test", "project": "demo"}' \
  -F channel=api \
  -F workflow=default
```

Respuesta:
```json
{
  "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "received"
}
```

### 4. Consultar el estado

```bash
curl http://localhost:8000/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Mientras se procesa:
```json
{
  "status": "extracting",
  "page_count": 4,
  "document_count": 3
}
```

Al completar:
```json
{
  "status": "completed",
  "page_count": 4,
  "document_count": 3,
  "result": {
    "documents": [
      {
        "doc_type": "invoice",
        "page_indices": [0, 1],
        "extracted_data": {
          "invoice_number": "F-2024-00142",
          "total_amount": 1250.00,
          "vendor_name": "Empresa ABC S.L."
        }
      }
    ]
  }
}
```

### 5. Resolver tareas manuales (si las hay)

Si algun documento se ha derivado a revision manual por baja confianza:

```
http://localhost:8001/?operator=mi_nombre
```

### 6. Comandos utiles

```bash
make up                  # Levantar todo
make down                # Parar todo
make logs                # Ver logs de todos los servicios
make logs-ocr            # Ver logs de un servicio concreto
make clean               # Parar todo y borrar volumenes
make migrate-docker      # Ejecutar migraciones en Docker
make test                # Ejecutar tests
make lint                # Linter
make format              # Auto-formatear codigo
```

---

## API Reference

### POST /process

Enviar un documento para procesamiento.

| Parametro | Tipo | Obligatorio | Default | Descripcion |
|---|---|---|---|---|
| `file` | File (multipart) | Si | - | Fichero a procesar |
| `metadata` | JSON string | No | `{}` | Metadatos arbitrarios del cliente |
| `channel` | string | No | `"api"` | Canal de entrada |
| `workflow` | string | No | `"default"` | Nombre del workflow a ejecutar |
| `external_id` | string | No | `null` | Referencia externa del cliente |

**Respuesta** `200`:
```json
{"request_id": "uuid", "status": "received"}
```

### GET /status/{request_id}

Consultar el estado de un trabajo.

**Respuesta** `200`:
```json
{
  "request_id": "uuid",
  "status": "completed|processing|failed|sla_breached",
  "workflow_name": "default",
  "created_at": "ISO-8601",
  "deadline_utc": "ISO-8601",
  "completed_at": "ISO-8601 | null",
  "page_count": 5,
  "document_count": 3,
  "result": {"documents": [...]},
  "error": "null | descripcion del error"
}
```

**Estados posibles**: `received` -> `routing` -> `splitting` -> `processing` -> `extracting` -> `consolidating` -> `completed`. Alternativos: `failed`, `sla_breached`.

### GET /health

```json
{"status": "ok"}
```

---

## Componentes del pipeline

### Resumen

| # | Componente | Cola | Tipo | Funcion |
|---|---|---|---|---|
| 1 | API Gateway | - (HTTP) | FastAPI | Recepcion de peticiones |
| 2 | Workflow Router | `q.workflow_router` | BaseComponent | Seleccion de flujo y SLA |
| 3 | Splitter | `q.splitter` | BaseComponent | Fan-out: fichero -> N paginas |
| 4 | OCR | `q.ocr` | BaseComponent | Extraccion de texto |
| 5 | Classifier | `q.classifier` | BaseComponent | Clasificacion por tipo documental |
| 6 | Classif. Aggregator | `q.classification_aggregator` | BaseComponent | Fan-in + agrupacion en documentos |
| 7 | Extractor | `q.extractor` | BaseComponent | Extraccion de datos estructurados |
| 8 | Extract. Aggregator | `q.extraction_aggregator` | BaseComponent | Fan-in de documentos |
| 9 | Consolidator | `q.consolidator` | BaseComponent | Ensamblaje del resultado |
| 10 | SLA Monitor | - (polling) | Independiente | Vigilancia de deadlines |
| 11 | Back Office | - (HTTP) | FastAPI | Interfaz para operadores |

### Estado actual de la implementacion

Los componentes de OCR, clasificacion y extraccion tienen implementaciones **stub** que devuelven datos simulados. Esto permite probar el flujo completo end-to-end. Para poner el sistema en produccion, se reemplazan los stubs por modelos ML reales sin cambiar la arquitectura.

| Componente | Implementacion actual |
|---|---|
| OCR | Devuelve texto de ejemplo aleatorio (factura, DNI, nomina...) |
| Classifier | Clasificacion por keywords + confianza aleatoria 0.60-0.99 |
| Extractor | Datos hardcodeados por tipo + confianza aleatoria 0.65-0.99 |

---

## Configuracion

Toda la configuracion se gestiona con variables de entorno con prefijo `DOCPROC_`. Definidas en `config/settings.py`.

| Variable | Default | Descripcion |
|---|---|---|
| `DOCPROC_COMPONENT_NAME` | `unknown` | Componente a ejecutar |
| `DOCPROC_RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | Conexion RabbitMQ |
| `DOCPROC_DATABASE_URL` | `postgresql+asyncpg://docproc:docproc@localhost:5432/docproc` | Conexion PostgreSQL |
| `DOCPROC_HEALTH_PORT` | `8080` | Puerto del health server |
| `DOCPROC_PREFETCH_COUNT` | `1` | Mensajes en vuelo por consumer |
| `DOCPROC_MESSAGE_TTL_MS` | `300000` | TTL de mensajes (5 min) |
| `DOCPROC_DEFAULT_SLA_SECONDS` | `60` | SLA por defecto |
| `DOCPROC_CLASSIFICATION_CONFIDENCE_THRESHOLD` | `0.80` | Umbral de clasificacion |
| `DOCPROC_EXTRACTION_CONFIDENCE_THRESHOLD` | `0.75` | Umbral de extraccion |
| `DOCPROC_BACKOFFICE_TASK_TIMEOUT_SECONDS` | `120` | Timeout de tareas manuales |
| `DOCPROC_STORAGE_PATH` | `/tmp/docproc/storage` | Ruta de almacenamiento |
| `DOCPROC_WORKFLOWS_DIR` | `config/workflows` | Directorio de workflows YAML |

Se puede usar un fichero `.env` (ver `.env.example`).

---

## Definicion de workflows

Los flujos se definen en `config/workflows/` como ficheros YAML. Ejemplo (`default.yaml`):

```yaml
name: default
description: "Standard document processing workflow"
version: 1

sla:
  deadline_seconds: 60            # Deadline en segundos desde la recepcion
  warn_threshold_pct: 70          # Alertar cuando se ha consumido el 70% del tiempo
  escalation_threshold_pct: 90    # Escalar cuando se ha consumido el 90%

stages:
  - name: split
    component: splitter
    routing_key: request.split
    timeout_seconds: 10

  - name: ocr
    component: ocr
    routing_key: page.ocr
    timeout_seconds: 15
    parallel: true                # Se ejecuta en paralelo (una instancia por pagina)

  - name: classify
    component: classifier
    routing_key: page.classify
    confidence_threshold: 0.80    # Por debajo de esto -> back office
    backoffice_queue: task.classification

  - name: classification_aggregation
    component: classification_aggregator
    routing_key: page.classified
    aggregation:
      type: fan_in
      collect_by: request_id
      expect_count_from: page_count

  - name: extract
    component: extractor
    routing_key: doc.extract
    confidence_threshold: 0.75
    backoffice_queue: task.extraction

  - name: extraction_aggregation
    component: extraction_aggregator
    routing_key: doc.extracted
    aggregation:
      type: fan_in
      collect_by: request_id
      expect_count_from: document_count

  - name: consolidate
    component: consolidator
    routing_key: request.consolidate

# Campos a extraer por tipo de documento
extraction_schemas:
  invoice:
    fields:
      - {name: invoice_number, type: string, required: true}
      - {name: total_amount, type: decimal, required: true}
      - {name: vendor_name, type: string}
      - {name: date, type: date}
  id_card:
    fields:
      - {name: full_name, type: string, required: true}
      - {name: id_number, type: string, required: true}
      - {name: date_of_birth, type: date}
  payslip:
    fields:
      - {name: company_name, type: string, required: true}
      - {name: employee_name, type: string, required: true}
      - {name: gross_amount, type: decimal}
      - {name: net_amount, type: decimal}
      - {name: period, type: string}
```

Para crear un flujo nuevo, basta con crear un nuevo fichero YAML y referenciarlo al enviar la peticion con `workflow=nombre_del_fichero`.

---

## Base de datos

PostgreSQL con 6 tablas gestionadas por SQLAlchemy ORM y migradas con Alembic.

### Diagrama de tablas

```
+-------------------+       +-------------------+       +-------------------+
|     requests      |       |      pages        |       |    documents      |
+-------------------+       +-------------------+       +-------------------+
| id (PK)           |<---+  | id (PK)           |  +--->| id (PK)           |
| external_id       |    |  | request_id (FK) --+--+    | request_id (FK) --+--+
| channel           |    |  | page_index        |       | doc_type          |  |
| workflow_name     |    |  | status            |       | page_indices[]    |  |
| status            |    |  | ocr_text          |       | status            |  |
| priority          |    |  | ocr_confidence    |       | extracted_data    |  |
| deadline_utc      |    |  | doc_type          |       | extraction_conf.  |  |
| sla_seconds       |    |  | classif_confidence|       +-------------------+  |
| page_count        |    |  | document_id (FK) -+--+                           |
| document_count    |    |  +-------------------+                              |
| result_payload    |    |                                                     |
| metadata          |    +-----------------------------------------------------+
+-------------------+
        |
        |    +-------------------+       +-------------------+
        |    | backoffice_tasks  |       |    operators      |
        |    +-------------------+       +-------------------+
        +--->| id (PK)           |       | id (PK)           |
             | request_id (FK)   |       | username (unique)  |
             | task_type         |       | display_name      |
             | reference_id      |       | skills[]          |
             | status            |       | is_active         |
             | priority          |       | current_task_id   |
             | assigned_to       |       +-------------------+
             | input_data        |
             | output_data       |
             | required_skills[] |
             +-------------------+

+-------------------+
| aggregation_state |
+-------------------+
| id (PK)           |
| request_id (FK)   |
| stage             |  (unique: request_id + stage)
| expected_count    |
| received_count    |
| is_complete       |
+-------------------+
```

### Estados de un request

```
received -> routing -> splitting -> processing -> extracting -> consolidating -> completed
                                                                            \-> failed
                                                                            \-> sla_breached
```

### Patron de fan-in atomico

Los aggregators usan la tabla `aggregation_state` para conteo concurrente seguro:

```sql
UPDATE aggregation_state
SET received_count = received_count + 1, updated_at = NOW()
WHERE request_id = :request_id AND stage = :stage
RETURNING received_count, expected_count
```

PostgreSQL garantiza atomicidad a nivel de fila. Si 5 paginas terminan simultaneamente y 5 workers ejecutan este UPDATE, cada uno obtiene un `received_count` distinto y solo el ultimo (donde `received == expected`) ejecuta la logica de agrupacion.

---

## Topologia RabbitMQ

### Exchanges

| Exchange | Tipo | Proposito |
|---|---|---|
| `doc.direct` | DIRECT | Pipeline principal |
| `doc.backoffice` | DIRECT | Tareas para operadores humanos |
| `doc.dlx` | FANOUT | Dead letter (mensajes fallidos/expirados) |

### Colas y bindings

```
doc.direct:
  request.new         --> q.workflow_router
  request.split       --> q.splitter
  page.ocr            --> q.ocr
  page.classify       --> q.classifier
  page.classified     --> q.classification_aggregator
  doc.extract         --> q.extractor
  doc.extracted       --> q.extraction_aggregator
  request.consolidate --> q.consolidator

doc.backoffice:
  task.classification --> q.backoffice.classification
  task.extraction     --> q.backoffice.extraction

doc.dlx:
  (fanout)            --> q.dead_letters
```

### Propiedades de cada cola

- `durable: true` (sobrevive restart del broker)
- `x-dead-letter-exchange: doc.dlx` (mensajes fallidos van a DLQ)
- `x-message-ttl: 300000` (5 minutos de vida maxima)
- `prefetch_count: 1` por consumer (fair dispatch)

### RabbitMQ Management UI

Accesible en `http://localhost:15672` (credenciales: `guest/guest`) para ver colas, mensajes en vuelo, rates, etc.

---

## Back Office - Interfaz de operadores

### Acceso

```
http://localhost:8001/?operator=nombre_del_operador
```

### Flujo del operador

1. **Dashboard**: tabla con todas las tareas pendientes, ordenadas por prioridad y antiguedad.
2. **Reclamar**: el operador toma una tarea. Se reserva para el y no aparece para otros.
3. **Resolver**: ve el texto OCR, el tipo sugerido o los datos parciales, y corrige lo necesario.
4. **Enviar**: la correccion se reinyecta en el pipeline (publica a `page.classified` o `doc.extracted`).

### API programatica

```bash
# Listar tareas pendientes
curl http://localhost:8001/api/tasks?status=pending

# Filtrar por skill
curl http://localhost:8001/api/tasks?status=pending&skill=classification

# Reclamar
curl -X POST http://localhost:8001/tasks/{id}/claim -F operator=juan

# Enviar clasificacion
curl -X POST http://localhost:8001/tasks/{id}/submit -F operator=juan -F doc_type=invoice

# Enviar extraccion
curl -X POST http://localhost:8001/tasks/{id}/submit \
  -F operator=juan \
  -F extracted_data='{"invoice_number": "F-001", "total_amount": 500}'
```

---

## Monitorizacion y SLA

### SLA Monitor

El componente `sla_monitor` consulta la BD cada 5 segundos buscando:

- **SLA incumplidos**: requests cuyo deadline ya paso. Se marcan como `sla_breached`.
- **SLA en riesgo**: requests con menos del 30% del tiempo restante. Se emite un warning en logs.

### Logging estructurado

Todos los componentes emiten logs JSON con structlog. Cada log incluye:

```json
{
  "event": "message_processed",
  "component": "classifier",
  "request_id": "uuid",
  "trace_id": "uuid",
  "elapsed_s": 0.234,
  "timestamp": "2024-01-15T10:30:05.123Z",
  "level": "info"
}
```

Campos disponibles para filtrado: `component`, `request_id`, `trace_id`, `event`, `level`.

### Health checks

Cada componente expone HTTP en su `DOCPROC_HEALTH_PORT`:

- `GET /health` -> 200 siempre (liveness)
- `GET /ready` -> 200 cuando esta consumiendo, 503 si no (readiness)

---

## Crear un nuevo componente

### 1. Crear el modulo

```python
# src/components/mi_componente/component.py

from src.core.base_component import BaseComponent
from src.core.schemas import PipelineMessage
from sqlalchemy.ext.asyncio import AsyncSession


class MiComponente(BaseComponent):

    component_name = "mi_componente"

    async def setup(self):
        """Inicializacion (opcional): cargar modelos, abrir conexiones..."""
        pass

    async def process_message(
        self, message: PipelineMessage, session: AsyncSession
    ) -> list[tuple[str, PipelineMessage]]:
        # Logica de negocio
        # session ya tiene transaccion abierta (auto-commit si no hay excepcion)

        resultado = message.model_copy(update={
            "source_component": self.component_name,
            "payload": {**message.payload, "nuevo_dato": "valor"},
        })
        return [("routing.key.siguiente", resultado)]
```

### 2. Registrarlo en el dispatcher

En `src/__main__.py`:

```python
COMPONENT_REGISTRY = {
    ...
    "mi_componente": "src.components.mi_componente.component.MiComponente",
}
```

### 3. Declarar su cola (si es nueva)

En `src/core/rabbitmq.py`:

```python
QUEUE_BINDINGS = {
    ...
    "q.mi_componente": ("doc.direct", "mi.routing.key"),
}
```

### 4. Anadirlo al docker-compose

```yaml
mi-componente:
  build: .
  command: python -m src
  environment:
    DOCPROC_COMPONENT_NAME: mi_componente
    DOCPROC_RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672/
    DOCPROC_DATABASE_URL: postgresql+asyncpg://docproc:docproc@postgres:5432/docproc
  depends_on:
    rabbitmq:
      condition: service_healthy
    postgres:
      condition: service_healthy
```

---

## Despliegue en Kubernetes

El directorio `k8s/` contiene manifiestos Kustomize:

```
k8s/
|-- base/                    # Configuracion base
|   |-- kustomization.yaml
|   |-- namespace.yaml
|   |-- rabbitmq/            # StatefulSet + Service
|   |-- postgres/             # StatefulSet + Service + PVC
|   |-- api-gateway/          # Deployment + Service + Ingress
|   |-- ocr/                  # Deployment + HPA
|   |-- classifier/           # Deployment + HPA
|   |-- extractor/            # Deployment + HPA
|   |-- ... (un directorio por componente)
|-- overlays/
    |-- dev/                  # 1 replica, debug logging
    |-- prod/                 # HPA, resource limits, multi-replica
```

### Escalado automatico

Los componentes CPU-intensivos (OCR, Classifier, Extractor) usan HPA basado en la profundidad de la cola de RabbitMQ:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: ocr
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Pods
      pods:
        metric:
          name: rabbitmq_queue_messages_ready
        target:
          type: AverageValue
          averageValue: "5"
```

---

## Desarrollo

### Instalacion local

```bash
# Dependencias de produccion
pip install -e .

# Con dependencias de desarrollo
pip install -e ".[dev]"
```

### Levantar solo la infraestructura

Para desarrollar un componente localmente contra RabbitMQ/PostgreSQL en Docker:

```bash
docker compose up -d rabbitmq postgres

# Ejecutar migraciones
alembic upgrade head

# Ejecutar un componente
DOCPROC_COMPONENT_NAME=ocr python -m src

# O ejecutar el API Gateway
uvicorn src.components.api_gateway.app:app --reload --port 8000
```

### Tests

```bash
make test          # pytest
make lint          # ruff check
make format        # ruff format
```

### Estructura de tests

```
tests/
|-- conftest.py                 # Fixtures: test DB, test RabbitMQ
|-- unit/
|   |-- test_base_component.py
|   |-- test_workflow_loader.py
|   |-- test_schemas.py
|   |-- test_sla.py
|-- integration/
|   |-- test_pipeline_e2e.py    # Flujo completo con stubs
|   |-- test_backoffice.py
|-- fixtures/
    |-- sample_request.json
    |-- workflows/test_workflow.yaml
```

---

## Documentacion de componentes

Documentacion detallada de cada componente en `docs/components/`:

| Documento | Contenido |
|---|---|
| [00-indice.md](docs/components/00-indice.md) | Mapa de navegacion |
| [01-core-framework.md](docs/components/01-core-framework.md) | BaseComponent, PipelineMessage, RabbitMQ, modelos ORM |
| [02-api-gateway.md](docs/components/02-api-gateway.md) | Punto de entrada HTTP |
| [03-workflow-router.md](docs/components/03-workflow-router.md) | Seleccion de flujo y SLA |
| [04-splitter.md](docs/components/04-splitter.md) | Descompresion y fan-out |
| [05-ocr.md](docs/components/05-ocr.md) | Extraccion de texto |
| [06-classifier.md](docs/components/06-classifier.md) | Clasificacion y routing por confianza |
| [07-classification-aggregator.md](docs/components/07-classification-aggregator.md) | Fan-in y agrupacion |
| [08-extractor.md](docs/components/08-extractor.md) | Extraccion de datos |
| [09-extraction-aggregator.md](docs/components/09-extraction-aggregator.md) | Fan-in de documentos |
| [10-consolidator.md](docs/components/10-consolidator.md) | Resultado final |
| [11-sla-monitor.md](docs/components/11-sla-monitor.md) | Vigilancia de SLA |
| [12-backoffice.md](docs/components/12-backoffice.md) | Interfaz de operadores |

Cada documento explica: **que hace** el componente, **como se utiliza** y **como esta implementado** internamente.
