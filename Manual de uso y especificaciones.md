# DocProc — Sistema Distribuido de Procesamiento Documental

## Documento de Especificaciones Técnicas y Manual de Uso

**Versión:** 1.0  
**Fecha:** Febrero 2026  
**Repositorio:** https://github.com/SergioST90/document-processing-system

---

# PARTE I — ESPECIFICACIONES TÉCNICAS

---

## 1. Visión General del Sistema

DocProc es un motor de orquestación distribuida para el tratamiento automatizado de documentos. El sistema recibe peticiones que contienen ficheros y metadatos, descompone los ficheros en páginas individuales, aplica reconocimiento óptico de caracteres (OCR) y clasificación automática por tipo documental, agrupa las páginas en documentos lógicos, extrae datos estructurados de cada documento y permite la intervención humana (human-in-the-loop) cuando la confianza de los procesos automáticos es insuficiente. Todo ello bajo gestión activa de SLAs con deadlines configurables que pueden ser tan agresivos como 30 segundos.

El sistema está diseñado como un grafo dirigido acíclico (DAG) de etapas configurables, donde cada flujo documental se define mediante ficheros YAML sin necesidad de modificar código. Los componentes son reutilizables, parametrizables y escalables de forma independiente.

### 1.1 Principios de Diseño

El sistema se rige por los siguientes principios fundamentales:

**Monorepo con imagen Docker única.** Todos los componentes comparten el framework core (`src/core/`). Existe un único `Dockerfile` multi-stage. La variable de entorno `DOCPROC_COMPONENT_NAME` determina qué componente ejecuta cada contenedor al arrancar. Esto simplifica el CI/CD y garantiza coherencia entre componentes.

**Componentes reutilizables y configurables.** Cada pieza del pipeline hereda de `BaseComponent`, una clase abstracta que gestiona la conexión a RabbitMQ, la sesión de base de datos, el logging estructurado, los health checks y el shutdown graceful. Un componente se desarrolla una sola vez y se reutiliza en N flujos distintos; lo único que varía es su configuración.

**Fan-out / Fan-in con estado atómico.** El Splitter genera N mensajes (uno por página) que se procesan en paralelo. Los Aggregators esperan a que lleguen todos los resultados usando conteo atómico en PostgreSQL mediante `UPDATE ... SET received_count = received_count + 1 RETURNING`, lo que garantiza consistencia incluso con múltiples workers concurrentes.

**Routing por confianza.** Las etapas de clasificación y extracción evalúan la confianza del resultado automático contra un umbral configurable. Si la confianza es insuficiente, el trabajo se desvía automáticamente al Back Office para intervención humana.

**Workflows declarativos.** Los flujos de procesamiento se definen íntegramente en ficheros YAML, sin necesidad de tocar código para crear, modificar o eliminar flujos.

**Gestión proactiva de SLA.** Cada trabajo tiene un deadline calculado desde el momento de su recepción. Un monitor dedicado revisa periódicamente los trabajos en curso y escala prioridades o emite respuestas parciales cuando detecta riesgo de incumplimiento.

### 1.2 Requisitos Cubiertos

El sistema ha sido diseñado para cubrir los siguientes requisitos operativos, tal como se describen en el documento de necesidades:

**Paralelización y subdivisión de trabajo** — mediante fan-out/fan-in a tres niveles: entre trabajos simultáneos (cola de mensajes con N workers), dentro de un trabajo (Splitter que genera N sub-trabajos por página) y entre etapas independientes de un mismo documento (extracciones paralelas que convergen en un Aggregator). Los Aggregators implementan puntos de control que evalúan en tiempo real si ya disponen de información suficiente para emitir una respuesta temprana.

**Estandarización, reutilización y escalabilidad dinámica** — cada etapa es una instancia parametrizada de un componente estándar centralizado. La misma imagen Docker sirve para cualquier componente; solo cambia la configuración. El escalado se realiza mediante Kubernetes HPA basado en la profundidad de las colas de RabbitMQ.

**Integración del Back Office (human-in-the-loop)** — con asignación automática de tareas a operadores según disponibilidad, skills y carga actual. Gestión de horarios, detección de presencia y reasignación automática si un operador no responde.

**Gestión de SLA y prioridades** — con deadlines calculados por trabajo, colas ordenadas por proximidad al vencimiento, monitor de SLA que detecta riesgos y escala prioridades o emite respuestas tempranas.

**Monitorización** — con logging estructurado JSON emitido por cada componente, métricas de tiempo, resultados, confianza y recursos consumidos, y dashboards en tiempo real con detección de anomalías.

**Entornos y despliegue controlado** — con pipeline CI/CD, juegos de pruebas acumulativos con respuestas esperadas, rollback automático si las pruebas fallan, y capacity planning en el momento del despliegue.

---

## 2. Arquitectura del Sistema

### 2.1 Diagrama de Arquitectura General

```
                          ┌──────────────────┐
                          │     Cliente      │
                          │  (HTTP / SFTP)   │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │   API Gateway    │  Puerto 8000
                          │   (FastAPI)      │
                          └────────┬─────────┘
                                   │
                          publica a RabbitMQ
                                   │
     ┌─────────────────────────────▼─────────────────────────────┐
     │                    PIPELINE (RabbitMQ)                     │
     │                                                           │
     │  [Workflow Router] → [Splitter] → fan-out N páginas       │
     │       │                              │                    │
     │       ▼                              ▼                    │
     │  Selecciona flujo             [OCR] × N páginas           │
     │  Calcula SLA                        │                     │
     │                                     ▼                     │
     │                            [Classifier] × N               │
     │                             /            \                │
     │                    alta conf.          baja conf.         │
     │                        │                   │              │
     │                        ▼                   ▼              │
     │             [Classif. Aggregator]     [Back Office]       │
     │              (fan-in + agrupar)       (operador)          │
     │                        │                   │              │
     │                        ◄── page.classified ┘              │
     │                        │                                  │
     │                   fan-out M docs                          │
     │                        │                                  │
     │                 [Extractor] × M                           │
     │                  /            \                           │
     │         alta conf.          baja conf.                    │
     │             │                   │                         │
     │             ▼                   ▼                         │
     │    [Extract. Aggregator]   [Back Office]                  │
     │         (fan-in)           (operador)                     │
     │             │                   │                         │
     │             ◄── doc.extracted ──┘                         │
     │             │                                             │
     │      [Consolidator]                                       │
     │      (resultado final)                                    │
     └──────────────────────────┬────────────────────────────────┘
                                │
                       ┌────────▼─────────┐
                       │  SLA Monitor     │  (polling cada 5s)
                       │  (vigila plazos) │
                       └──────────────────┘
```

### 2.2 Stack Tecnológico

El sistema utiliza el siguiente stack:

Python >= 3.11 como lenguaje principal, con todo el pipeline basado en `asyncio` nativo. FastAPI + Uvicorn para los endpoints HTTP (API Gateway y Back Office). RabbitMQ 3.13 como broker de mensajes, con `aio-pika` 9.4+ como cliente async. PostgreSQL 16 como base de datos, con SQLAlchemy 2.0+ (async) como ORM y Alembic 1.13+ para migraciones. Pydantic v2 para la validación de schemas y `pydantic-settings` para la configuración. `structlog` para logging estructurado en formato JSON. Docker + Docker Compose para contenerización local y Kubernetes + Kustomize para despliegue en producción.

### 2.3 Modelo de Comunicación

Toda la comunicación entre componentes del pipeline se realiza mediante mensajes asíncronos a través de RabbitMQ. No hay llamadas directas entre servicios (salvo HTTP hacia el API Gateway y Back Office). Cada componente consume de una cola específica, procesa el mensaje, actualiza el estado en PostgreSQL y publica el resultado en la cola de la siguiente etapa.

El envelope universal que viaja por todas las colas es el `PipelineMessage`, un modelo Pydantic que contiene: `request_id` (UUID del trabajo), `trace_id` (UUID de traza para observabilidad), `workflow_name` (nombre del workflow YAML), `current_stage` (etapa actual del workflow, usada para el enrutamiento dinámico), `source_component` (nombre del componente que generó el mensaje), `payload` (datos específicos de la etapa), `page_index` (opcional, para mensajes de página individual), `document_id` (opcional, para mensajes de documento) y `deadline_utc` (deadline del SLA).

---

## 3. Flujo de Procesamiento Detallado

### 3.1 Etapa 0 — Recepción y Routing

**Componente:** API Gateway (FastAPI, puerto 8000) + Workflow Router

El cliente envía un fichero junto con metadatos a través de `POST /process`. El API Gateway almacena el fichero en el directorio de storage, crea un registro en la tabla `requests` de PostgreSQL con estado `received` y publica un mensaje en la cola `q.workflow_router` del exchange `doc.direct`.

El Workflow Router consume ese mensaje, lee los metadatos y el canal de entrada para determinar qué workflow YAML ejecutar. Carga la definición del flujo, calcula el deadline del SLA (`timestamp_entrada + sla_seconds`) y actualiza el registro del request con el deadline, el nombre del workflow y el estado `routing`. A continuación, consulta el `WorkflowLoader` para obtener la primera etapa del workflow mediante `get_first_stage()` y publica el mensaje hacia la cola correspondiente (en el workflow default es `q.splitter`), estableciendo `current_stage` en el mensaje.

**Punto crítico:** el SLA Timer arranca en este momento exacto. Con SLAs de 30 segundos, cada milisegundo cuenta desde la recepción.

### 3.2 Etapa 1 — Descompresión y Fan-Out por Página

**Componente:** Splitter

El Splitter recibe el mensaje con la referencia al fichero, lo descompone en páginas individuales (cada página del PDF, TIFF, etc.) y genera un sub-trabajo por cada página. Actualiza la tabla `requests` con el `page_count` total y crea una fila en la tabla `pages` por cada página con estado `PENDIENTE_OCR`. También inicializa la tabla `aggregation_state` con `stage='classification'`, `expected_count=N` y `received_count=0`.

Cada página se publica como un mensaje independiente usando el sentinela `__next__`, que el framework resuelve dinámicamente consultando el workflow YAML (en el workflow default, la siguiente etapa es OCR con cola `q.ocr`). Este es el primer punto de fan-out: si un fichero tiene 25 páginas, se generan 25 mensajes que N workers procesarán en paralelo.

El estado del request pasa a `splitting` y luego a `processing`.

### 3.3 Etapa 2 — OCR y Extracción de Texto

**Componente:** OCR

Cada worker OCR consume un mensaje de la cola `q.ocr`, procesa una página individual para extraer el texto mediante reconocimiento óptico de caracteres y actualiza la fila correspondiente en la tabla `pages` con el texto extraído y la confianza del OCR. A continuación, devuelve el sentinela `__next__` y el framework resuelve dinámicamente la siguiente etapa del workflow (en el workflow default, la cola `q.classifier`).

**Estado actual (stub):** la implementación actual devuelve texto de ejemplo aleatorio simulando diferentes tipos documentales (factura, DNI, nómina). Para producción, se sustituye por un motor OCR real (Tesseract, Google Vision, AWS Textract, etc.) sin cambiar la arquitectura.

### 3.4 Etapa 3 — Clasificación por Tipo Documental

**Componente:** Classifier

El Classifier recibe el texto de cada página y determina su tipo documental (factura, DNI, nómina, contrato, etc.) junto con una puntuación de confianza. El umbral de confianza es configurable por flujo (por defecto 0.80, definido en `DOCPROC_CLASSIFICATION_CONFIDENCE_THRESHOLD` o en el YAML del workflow).

Si la confianza es igual o superior al umbral, la página se marca como clasificada automáticamente y se devuelve el sentinela `__next__`, que el framework resuelve dinámicamente a la siguiente etapa del workflow (en el workflow default, `q.classification_aggregator` con routing key `page.classified`).

Si la confianza es inferior al umbral, se crea una tarea en la tabla `backoffice_tasks` con `task_type='classification'`, `source_stage` (etapa actual) y `workflow_name`, y se devuelve el sentinela `__backoffice__`, que el framework resuelve consultando el `backoffice_queue` configurado en la etapa actual del YAML (por defecto `task.classification`, que va a la cola `q.backoffice.classification` del exchange `doc.backoffice`). La página queda pendiente hasta que un operador la clasifique manualmente.

**Estado actual (stub):** clasificación por keywords en el texto OCR con confianza aleatoria entre 0.60 y 0.99. Para producción, se sustituye por un modelo ML real.

### 3.5 Intervención Humana — Clasificación Manual

**Componente:** Back Office (FastAPI, puerto 8001)

Cuando una página se deriva al Back Office por baja confianza en la clasificación, el operador accede al dashboard en `http://localhost:8001/?operator=nombre`, ve la tarea pendiente con la imagen de la página y el texto OCR extraído, reclama la tarea (que se reserva para él y desaparece para otros operadores), clasifica manualmente el tipo documental y envía la corrección.

Al enviar, el Back Office actualiza la fila en `pages` con el tipo documental corregido, confianza 1.0 y origen `BACKOFFICE`, actualiza la tarea como `completed` y reinyecta la página en el pipeline consultando dinámicamente el workflow YAML: lee `source_stage` y `workflow_name` de la tarea y usa `WorkflowLoader.get_next_stage()` para determinar la siguiente etapa (en el workflow default, `q.classification_aggregator` con routing key `page.classified`).

### 3.6 Etapa 4 — Agrupación de Páginas en Documentos (Fan-In)

**Componente:** Classification Aggregator

Este es el primer punto de sincronización (Join). Cada vez que llega un mensaje `page.classified`, el Aggregator ejecuta un `UPDATE atómico` en la tabla `aggregation_state`:

```sql
UPDATE aggregation_state
SET received_count = received_count + 1, updated_at = NOW()
WHERE request_id = :request_id AND stage = 'classification'
RETURNING received_count, expected_count
```

PostgreSQL garantiza atomicidad a nivel de fila. Si 5 páginas terminan simultáneamente y 5 workers ejecutan este UPDATE, cada uno obtiene un `received_count` distinto. Solo el último worker (donde `received_count == expected_count`) ejecuta la lógica de agrupación.

La agrupación consiste en ordenar las páginas clasificadas por su `page_index` y agrupar páginas consecutivas del mismo tipo documental en un mismo documento lógico. Por ejemplo, si las páginas 1-2 son DNI y las páginas 3-5 son Factura, se crean dos documentos lógicos.

El Aggregator crea filas en la tabla `documents`, actualiza `requests.document_count`, inicializa un nuevo registro en `aggregation_state` con `stage='extraction'` y `expected_count=M` (número de documentos), y devuelve el sentinela `__next__` por cada documento. El framework resuelve dinámicamente la siguiente etapa del workflow (en el workflow default, `q.extractor` con routing key `doc.extract`). Este es el segundo fan-out.

**Diseño con SLAs agresivos:** el Aggregator puede implementar un pipeline escalonado, agrupando y enviando a extracción los documentos ya completos sin esperar a que todas las páginas estén clasificadas. Así, la extracción arranca mientras aún se clasifican otras páginas.

### 3.7 Etapa 5 — Extracción de Datos Estructurados

**Componente:** Extractor

Cada documento agrupado se procesa para extraer sus campos según su tipo documental. La configuración de qué campos extraer para cada tipo se define en la sección `extraction_schemas` del workflow YAML.

Ejemplo de campos por tipo:

- **Factura:** invoice_number, total_amount, vendor_name, date
- **DNI / ID Card:** full_name, id_number, date_of_birth
- **Nómina / Payslip:** company_name, employee_name, gross_amount, net_amount, period

Cada campo extraído tiene asociada una confianza. Si algún campo tiene confianza inferior al umbral de extracción (por defecto 0.75, configurable en `DOCPROC_EXTRACTION_CONFIDENCE_THRESHOLD`), se crea una tarea en el Back Office con `task_type='extraction'`.

A diferencia de la clasificación (donde va la página entera), aquí se envía el documento con los campos ya rellenados y se marcan solo los campos que necesitan revisión. El operador ve el documento, lo que ya se extrajo automáticamente, y solo completa o corrige lo que falta.

**Estado actual (stub):** datos hardcodeados por tipo con confianza aleatoria entre 0.65 y 0.99.

### 3.8 Intervención Humana — Extracción Manual

**Componente:** Back Office

El operador ve una tarea de extracción donde aparecen los campos ya completados (marcados como correctos) y los campos pendientes de revisión. Completa los datos faltantes, envía la corrección, y el Back Office reinyecta el resultado consultando dinámicamente el workflow YAML mediante `source_stage` y `workflow_name` de la tarea (en el workflow default, la siguiente etapa tras `extract` es `extraction_aggregation` con routing key `doc.extracted`).

### 3.9 Etapa 6 — Consolidación y Respuesta (Fan-In Final)

**Componente:** Extraction Aggregator + Consolidator

El Extraction Aggregator funciona de forma idéntica al Classification Aggregator: conteo atómico en `aggregation_state` con `stage='extraction'`. Cuando todos los documentos están extraídos, devuelve el sentinela `__next__` que el framework resuelve dinámicamente a la siguiente etapa (en el workflow default, `q.consolidator` con routing key `request.consolidate`).

El Consolidator ensambla el resultado final: recopila todos los documentos extraídos de todos los ficheros del input original, construye el `result_payload` JSON y actualiza la tabla `requests` con `status='completed'` y el resultado final.

El cliente puede consultar el resultado en cualquier momento mediante `GET /status/{request_id}`.

### 3.10 Respuestas Parciales y Tempranas

Con SLAs de 30 segundos a 1 minuto, el sistema implementa una estrategia proactiva de respuestas tempranas. Los Aggregators y el SLA Monitor pueden decidir emitir una respuesta parcial con la información disponible hasta el momento si detectan riesgo de incumplimiento del SLA. Los documentos pendientes se entregan en una segunda respuesta cuando completan su procesamiento.

---

## 4. Modelo de Datos

### 4.1 Tablas de PostgreSQL

El sistema utiliza 6 tablas gestionadas por SQLAlchemy ORM y migradas con Alembic:

**`requests`** — Tabla central de tracking. Una fila por cada petición recibida. Campos principales: `id` (UUID, PK), `external_id` (referencia externa del cliente), `channel` (canal de entrada: api, sftp, email...), `workflow_name` (nombre del flujo YAML), `status` (estado actual del request), `priority` (prioridad numérica), `deadline_utc` (timestamp del deadline SLA), `sla_seconds` (duración del SLA), `page_count` (total de páginas detectadas), `document_count` (total de documentos lógicos), `result_payload` (JSONB con el resultado final), `metadata` (JSONB con metadatos del cliente), `created_at`, `updated_at`, `completed_at`.

**`pages`** — Una fila por cada página extraída de los ficheros. Campos: `id` (PK), `request_id` (FK a requests), `page_index` (índice de la página dentro del fichero), `status` (estado de procesamiento), `ocr_text` (texto extraído por OCR), `ocr_confidence` (confianza del OCR), `doc_type` (tipo documental clasificado), `classification_confidence` (confianza de la clasificación), `document_id` (FK a documents, asignado tras la agrupación).

**`documents`** — Documentos lógicos formados por la agrupación de páginas. Campos: `id` (PK), `request_id` (FK a requests), `doc_type` (tipo documental), `page_indices` (array de integers con los índices de las páginas que forman el documento), `status` (estado de procesamiento), `extracted_data` (JSONB con los campos extraídos), `extraction_confidence` (confianza mínima entre los campos).

**`backoffice_tasks`** — Tareas manuales para operadores. Campos: `id` (PK), `request_id` (FK a requests), `task_type` (classification o extraction), `reference_id` (ID de la página o documento referenciado), `status` (pending, assigned, completed, timeout), `priority` (numérica), `assigned_to` (FK a operators), `input_data` (JSONB con la información para el operador), `output_data` (JSONB con la corrección del operador), `required_skills` (array de strings con skills necesarios), `source_stage` (etapa del workflow que derivó al backoffice, para reinyección dinámica), `workflow_name` (nombre del workflow, para resolver la siguiente etapa al reinyectar), `created_at`, `assigned_at`, `completed_at`.

**`operators`** — Catálogo de operadores del Back Office. Campos: `id` (PK), `username` (unique), `display_name`, `skills` (array de strings: qué tipos de tarea sabe hacer), `is_active` (booleano de disponibilidad), `current_task_id` (FK a backoffice_tasks, la tarea que tiene asignada actualmente).

**`aggregation_state`** — Estado de los puntos de fan-in. Campos: `id` (PK), `request_id` (FK a requests), `stage` (string: 'classification' o 'extraction'), `expected_count` (total esperado), `received_count` (recibidos hasta ahora), `is_complete` (booleano). La combinación `(request_id, stage)` es unique.

### 4.2 Ciclo de Vida de un Request (Estados)

```
received → routing → splitting → processing → extracting → consolidating → completed
                                                                        ↘ failed
                                                                        ↘ sla_breached
```

El estado `processing` cubre la fase de OCR + clasificación + agrupación. El estado `extracting` cubre la fase de extracción de datos. Los estados terminales son `completed` (éxito), `failed` (error irrecuperable) y `sla_breached` (SLA incumplido).

---

## 5. Topología RabbitMQ

### 5.1 Exchanges

El sistema declara tres exchanges:

`doc.direct` (tipo DIRECT) — pipeline principal, por donde fluyen todos los mensajes de procesamiento. `doc.backoffice` (tipo DIRECT) — tareas para operadores humanos. `doc.dlx` (tipo FANOUT) — dead letter exchange, recibe mensajes fallidos o expirados.

### 5.2 Colas y Bindings

**Pipeline principal (exchange `doc.direct`):**

| Routing Key | Cola | Consumidor |
|---|---|---|
| `request.new` | `q.workflow_router` | Workflow Router |
| `request.split` | `q.splitter` | Splitter |
| `page.ocr` | `q.ocr` | OCR |
| `page.classify` | `q.classifier` | Classifier |
| `page.classified` | `q.classification_aggregator` | Classification Aggregator |
| `doc.extract` | `q.extractor` | Extractor |
| `doc.extracted` | `q.extraction_aggregator` | Extraction Aggregator |
| `request.consolidate` | `q.consolidator` | Consolidator |

**Back Office (exchange `doc.backoffice`):**

| Routing Key | Cola | Consumidor |
|---|---|---|
| `task.classification` | `q.backoffice.classification` | Back Office |
| `task.extraction` | `q.backoffice.extraction` | Back Office |

**Dead letter (exchange `doc.dlx`):**

| Routing | Cola | Consumidor |
|---|---|---|
| (fanout) | `q.dead_letters` | DLQ Consumer |

### 5.3 Propiedades de las Colas

Todas las colas del pipeline se configuran con: `durable: true` (sobreviven al reinicio del broker), `x-dead-letter-exchange: doc.dlx` (mensajes fallidos van a la DLQ), `x-message-ttl: 300000` (5 minutos de vida máxima por mensaje) y `prefetch_count: 1` por consumer (fair dispatch, cada worker recibe un solo mensaje hasta que lo acknowledge).

### 5.4 Flujo Completo del Mensaje (Happy Path)

```
POST /process → q.workflow_router → q.splitter → [fan-out N páginas]
  → q.ocr (×N) → q.classifier (×N)
    ├─ confianza alta → q.classification_aggregator [fan-in]
    └─ confianza baja → backoffice → operador corrige → q.classification_aggregator
  → [fan-out M documentos] → q.extractor (×M)
    ├─ confianza alta → q.extraction_aggregator [fan-in]
    └─ confianza baja → backoffice → operador completa → q.extraction_aggregator
  → q.consolidator → COMPLETADO (GET /status devuelve resultado)
```

---

## 6. Definición de Workflows (YAML)

### 6.1 Estructura de un Workflow

Cada flujo de procesamiento se define como un fichero YAML en `config/workflows/`. La estructura tiene tres secciones principales:

**Sección `sla`:** define el deadline en segundos desde la recepción del trabajo, el porcentaje de tiempo consumido a partir del cual se emite un warning (`warn_threshold_pct`) y el porcentaje a partir del cual se escala (`escalation_threshold_pct`).

**Sección `stages`:** lista ordenada de las etapas del pipeline. El **orden de las etapas define el flujo**: los componentes usan sentinelas (`__next__`, `__backoffice__`) y el framework consulta esta lista para resolver dinámicamente a qué etapa enrutar. Cada etapa especifica: `name` (identificador de la etapa), `component` (nombre del componente que la ejecuta), `routing_key` (routing key de RabbitMQ para la cola del componente), `timeout_seconds` (timeout individual de la etapa), `parallel` (booleano, indica si se ejecuta en paralelo por página/documento), `confidence_threshold` (umbral de confianza para desvío a manual), `backoffice_queue` (routing key del Back Office para tareas manuales) y configuración de agregación (`aggregation.type`, `aggregation.collect_by`, `aggregation.expect_count_from`).

**Sección `extraction_schemas`:** define los campos a extraer por cada tipo documental, incluyendo nombre, tipo de dato (string, decimal, date) y si es obligatorio.

### 6.2 Ejemplo Completo: Workflow `default`

```yaml
name: default
description: "Standard document processing workflow"
version: 1

sla:
  deadline_seconds: 60
  warn_threshold_pct: 70
  escalation_threshold_pct: 90

stages:
  - name: split
    component: splitter
    routing_key: request.split
    timeout_seconds: 10

  - name: ocr
    component: ocr
    routing_key: page.ocr
    timeout_seconds: 15
    parallel: true

  - name: classify
    component: classifier
    routing_key: page.classify
    confidence_threshold: 0.80
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

### 6.3 Crear un Nuevo Flujo

Para crear un flujo nuevo basta con crear un nuevo fichero YAML en `config/workflows/` (por ejemplo `hipotecas.yaml`) y referenciarlo al enviar la petición con `workflow=hipotecas`. Gracias al enrutamiento dinámico por sentinelas, los componentes no tienen hardcodeado a dónde emiten: consultan el workflow YAML para resolver la siguiente etapa. Esto permite reordenar etapas, saltar etapas o crear flujos completamente distintos sin modificar código. Solo es necesario que las etapas del nuevo workflow sean compatibles en datos de entrada/salida entre sí.

---

## 7. Gestión de SLA

### 7.1 Cálculo del Deadline

Cada trabajo recibe un SLA definido por su workflow (campo `sla.deadline_seconds`). En el momento de crear el registro en la tabla `requests`, se calcula: `deadline_utc = created_at + timedelta(seconds=sla_seconds)`. Este deadline acompaña al trabajo durante todo su ciclo de vida.

### 7.2 Monitor de SLA

El componente `sla_monitor` ejecuta un bucle de polling cada 5 segundos que consulta la base de datos buscando:

**SLA incumplidos:** requests cuyo `deadline_utc` ya ha pasado y no están en estado terminal. Se marcan como `sla_breached` y se emite un log de nivel ERROR.

**SLA en riesgo:** requests con menos del 30% del tiempo restante y etapas pendientes. Se emite un log de nivel WARNING y se puede escalar la prioridad del trabajo y sus tareas manuales asociadas.

### 7.3 Estrategia Proactiva para SLAs Agresivos

Con SLAs de 30 segundos, la estrategia es proactiva:

En el momento de la entrada (0s), se establecen checkpoints a intervalos proporcionales. Cuando se ha consumido el 70% del SLA (warn_threshold), se evalúa si el trabajo va bien o tiene riesgo. Cuando se ha consumido el 90% (escalation_threshold), se ejecutan acciones: pre-calcular respuesta parcial con los datos disponibles, escalar la prioridad de tareas manuales al máximo, asignar más workers si es posible.

Los workers de las primeras etapas (descompresión, OCR) deben estar siempre listos en modo hot standby, sin esperar al escalado dinámico que tardaría demasiado. El escalado dinámico aplica para capacidad extra, pero la base debe estar siempre disponible.

---

## 8. Back Office — Sistema de Intervención Humana

### 8.1 Motor de Asignación

El Back Office implementa un motor de asignación inteligente que gestiona: un catálogo de operadores donde cada uno tiene un perfil con skills (qué tipos de tarea sabe hacer), horarios y estado (activo/inactivo/ocupado); colas priorizadas por operador donde, cuando un trabajo necesita intervención manual, el motor busca operadores disponibles, con el skill requerido y menor carga actual; y heartbeat/presencia, donde el sistema detecta si el operador realmente está activo y si no responde en un tiempo configurable (`DOCPROC_BACKOFFICE_TASK_TIMEOUT_SECONDS`, por defecto 120s), la tarea se reasigna.

### 8.2 Interfaz Web del Operador

El operador accede a `http://localhost:8001/?operator=nombre_del_operador` y ve un dashboard con las tareas pendientes ordenadas por prioridad y antigüedad.

**Para tareas de clasificación:** el operador ve la imagen de la página, el texto OCR extraído y la clasificación sugerida con su confianza. Selecciona el tipo documental correcto y envía.

**Para tareas de extracción:** el operador ve el documento completo, los campos ya rellenados automáticamente (marcados con ✓) y los campos pendientes de revisión. Solo completa o corrige lo que falta.

### 8.3 API del Back Office

El Back Office expone una API REST para integración programática:

`GET /api/tasks?status=pending` — listar tareas pendientes, opcionalmente filtrando por `skill`.
`POST /tasks/{id}/claim` — reclamar una tarea (parámetro: `operator`).
`POST /tasks/{id}/submit` — enviar corrección (parámetros según `task_type`: `doc_type` para clasificación, `extracted_data` JSON para extracción).

---

## 9. Observabilidad y Monitorización

### 9.1 Logging Estructurado

Todos los componentes emiten logs JSON mediante `structlog`. Cada evento de log incluye como mínimo: `event` (nombre del evento), `component` (nombre del componente), `request_id` (UUID del trabajo), `trace_id` (UUID de traza), `elapsed_s` (tiempo transcurrido), `timestamp` (ISO-8601) y `level` (info, warning, error).

El esquema completo de un log de procesamiento incluye además: `etapa`, `pagina_id` o `document_id`, `timestamp_inicio`, `timestamp_fin`, `duracion_ms`, sección `resultado` (con tipo documental, confianza, si se derivó a manual), sección `recursos` (cpu_pct, memoria_mb, gpu_utilizada) y sección `sla` (deadline, restante_ms).

### 9.2 Métricas del Dashboard

Los logs alimentan dashboards en tiempo real con los siguientes ejes:

**Throughput:** páginas/minuto procesadas por etapa. **Latencia:** tiempo medio por etapa, P95, P99. **Confianza:** porcentaje de desvíos a manual por etapa y tipo documental. **SLA:** trabajos en riesgo, cumplimiento histórico, porcentaje de SLA breached. **Recursos:** CPU/memoria por componente, workers activos. **Back Office:** operadores activos, cola pendiente, tiempo medio de resolución.

### 9.3 Alertas Automáticas

El sistema genera alertas cuando: la cola de clasificación manual supera las 50 tareas (alerta de capacidad), el tiempo medio de OCR duplica la media histórica (posible degradación del servicio), los operadores disponibles son menos que las tareas pendientes urgentes (alerta de recursos humanos), un trabajo tiene menos del 20% del SLA restante con etapas pendientes (escalado automático), o el volumen a una hora determinada es un 40% superior a la media histórica de ese día/hora (alerta de escalado de recursos).

### 9.4 Health Checks

Cada componente del pipeline expone un servidor HTTP en el puerto definido por `DOCPROC_HEALTH_PORT` (por defecto 8080) con dos endpoints: `GET /health` que devuelve 200 siempre (liveness probe para Kubernetes) y `GET /ready` que devuelve 200 cuando el componente está consumiendo mensajes activamente y 503 si aún no está listo (readiness probe).

---

## 10. Configuración del Sistema

### 10.1 Variables de Entorno

Toda la configuración se gestiona con variables de entorno con prefijo `DOCPROC_`, definidas en `config/settings.py` usando `pydantic-settings`:

| Variable | Default | Descripción |
|---|---|---|
| `DOCPROC_COMPONENT_NAME` | `unknown` | Componente a ejecutar en este contenedor |
| `DOCPROC_RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | URL de conexión a RabbitMQ |
| `DOCPROC_DATABASE_URL` | `postgresql+asyncpg://docproc:docproc@localhost:5432/docproc` | URL de conexión a PostgreSQL |
| `DOCPROC_HEALTH_PORT` | `8080` | Puerto del servidor de health checks |
| `DOCPROC_PREFETCH_COUNT` | `1` | Mensajes en vuelo por consumer RabbitMQ |
| `DOCPROC_MESSAGE_TTL_MS` | `300000` | TTL de mensajes en colas (5 min) |
| `DOCPROC_DEFAULT_SLA_SECONDS` | `60` | SLA por defecto si no se define en workflow |
| `DOCPROC_CLASSIFICATION_CONFIDENCE_THRESHOLD` | `0.80` | Umbral de confianza para clasificación |
| `DOCPROC_EXTRACTION_CONFIDENCE_THRESHOLD` | `0.75` | Umbral de confianza para extracción |
| `DOCPROC_BACKOFFICE_TASK_TIMEOUT_SECONDS` | `120` | Timeout de tareas manuales antes de reasignar |
| `DOCPROC_STORAGE_PATH` | `/tmp/docproc/storage` | Ruta de almacenamiento de ficheros |
| `DOCPROC_WORKFLOWS_DIR` | `config/workflows` | Directorio de workflows YAML |

Se puede usar un fichero `.env` en la raíz del proyecto (ver `.env.example` como referencia).

---

## 11. Infraestructura y Despliegue

### 11.1 Docker Compose (Entorno Local)

El fichero `docker-compose.yml` levanta 12 servicios: RabbitMQ (con management UI en puerto 15672), PostgreSQL (puerto 5432), API Gateway (puerto 8000), Back Office (puerto 8001), y 8 componentes del pipeline (Workflow Router, Splitter, OCR, Classifier, Classification Aggregator, Extractor, Extraction Aggregator, Consolidator) más el SLA Monitor.

Todos los componentes del pipeline comparten la misma imagen Docker. Cada servicio solo difiere en la variable `DOCPROC_COMPONENT_NAME`.

### 11.2 Dockerfile

El Dockerfile es multi-stage: la primera stage instala dependencias y la segunda copia solo lo necesario para ejecutar. El entrypoint es `python -m src`, que lee `DOCPROC_COMPONENT_NAME` y arranca el componente correspondiente mediante el dispatcher definido en `src/__main__.py`.

### 11.3 Kubernetes

El directorio `k8s/` contiene manifiestos Kustomize organizados en `base/` (configuración base con un directorio por componente: namespace, deployments, services, StatefulSets para RabbitMQ y PostgreSQL) y `overlays/` con variantes para `dev/` (1 réplica, debug logging) y `prod/` (HPA, resource limits, multi-réplica).

### 11.4 Escalado Automático (HPA)

Los componentes CPU-intensivos (OCR, Classifier, Extractor) usan HorizontalPodAutoscaler basado en la métrica `rabbitmq_queue_messages_ready` (profundidad de la cola). Ejemplo: si hay más de 5 mensajes pendientes de media por pod en la cola de OCR, se escalan más réplicas, hasta un máximo de 10.

### 11.5 Entornos y Despliegue Controlado

El sistema contempla tres entornos: Desarrollo, Test y Producción. El flujo de despliegue es: Desarrollo → deploy → Test (se ejecuta el juego de pruebas con respuestas esperadas) → deploy → Producción (si las pruebas pasan; rollback automático si fallan).

Cada flujo tiene asociado un juego de pruebas acumulativo: casos de entrada con salidas esperadas que se crean durante el desarrollo y se acumulan con el tiempo. Al promover de Test a Producción, se ejecuta el juego de pruebas automáticamente.

En el momento del despliegue a producción se introducen parámetros de capacity planning (volumen esperado, media de páginas, tiempo máximo) y el sistema calcula cuántas instancias/workers necesita cada componente.

---

# PARTE II — MANUAL DE USO

---

## 12. Guía de Inicio Rápido

### 12.1 Requisitos Previos

Docker y Docker Compose instalados. Opcionalmente, Python 3.11+ para desarrollo local sin contenedores.

### 12.2 Clonar e Iniciar

```bash
git clone https://github.com/SergioST90/document-processing-system.git
cd document-processing-system

# Construir y levantar todos los servicios (12 contenedores)
docker compose up --build -d
```

Esto levanta: RabbitMQ en `localhost:5672` (management UI en `localhost:15672`, credenciales: `guest/guest`), PostgreSQL en `localhost:5432` (user/pass/db: `docproc`), API Gateway en `localhost:8000`, Back Office en `localhost:8001` y 8 componentes del pipeline más el SLA Monitor.

### 12.3 Ejecutar Migraciones de Base de Datos

```bash
docker compose exec api-gateway alembic upgrade head
```

Esto crea las 6 tablas del schema en PostgreSQL.

### 12.4 Enviar un Documento para Procesamiento

```bash
curl -X POST http://localhost:8000/process \
  -F file=@mi_documento.pdf \
  -F metadata='{"client": "acme", "project": "hipotecas"}' \
  -F channel=api \
  -F workflow=default
```

Respuesta:
```json
{"request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "status": "received"}
```

### 12.5 Consultar el Estado

```bash
curl http://localhost:8000/status/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

Mientras se procesa:
```json
{"status": "extracting", "page_count": 4, "document_count": 3}
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

### 12.6 Resolver Tareas Manuales

Si algún documento se ha derivado a revisión manual por baja confianza, abrir en el navegador:

```
http://localhost:8001/?operator=mi_nombre
```

---

## 13. Referencia de la API

### 13.1 POST /process

Envía un documento para procesamiento.

**Parámetros (multipart/form-data):**

| Parámetro | Tipo | Obligatorio | Default | Descripción |
|---|---|---|---|---|
| `file` | File | Sí | — | Fichero a procesar (PDF, TIFF, ZIP...) |
| `metadata` | JSON string | No | `{}` | Metadatos arbitrarios del cliente |
| `channel` | string | No | `"api"` | Canal de entrada (api, sftp, email...) |
| `workflow` | string | No | `"default"` | Nombre del workflow YAML a ejecutar |
| `external_id` | string | No | `null` | Referencia externa del cliente |

**Respuesta 200:**
```json
{"request_id": "uuid", "status": "received"}
```

### 13.2 GET /status/{request_id}

Consulta el estado de un trabajo.

**Respuesta 200:**
```json
{
  "request_id": "uuid",
  "status": "completed|processing|failed|sla_breached",
  "workflow_name": "default",
  "created_at": "2024-01-15T10:30:00Z",
  "deadline_utc": "2024-01-15T10:31:00Z",
  "completed_at": "2024-01-15T10:30:28Z",
  "page_count": 5,
  "document_count": 3,
  "result": {"documents": [...]},
  "error": null
}
```

**Estados posibles:** `received` → `routing` → `splitting` → `processing` → `extracting` → `consolidating` → `completed`. Alternativos: `failed`, `sla_breached`.

### 13.3 GET /health

```json
{"status": "ok"}
```

### 13.4 API del Back Office

`GET /api/tasks?status=pending&skill=classification` — listar tareas pendientes filtrando opcionalmente por skill.

`POST /tasks/{id}/claim` — reclamar una tarea. Parámetro form: `operator=nombre`.

`POST /tasks/{id}/submit` — enviar corrección. Para clasificación: `operator=nombre&doc_type=invoice`. Para extracción: `operator=nombre&extracted_data={"campo": "valor"}`.

---

## 14. Guía de Operación del Back Office

### 14.1 Flujo de Trabajo del Operador

El operador accede al dashboard en `http://localhost:8001/?operator=su_nombre` donde ve una tabla con todas las tareas pendientes ordenadas por prioridad (las más urgentes primero) y antigüedad. El operador selecciona una tarea, la reclama (queda reservada para él y desaparece para otros operadores), resuelve la tarea (clasificando la página o completando campos faltantes) y envía la corrección.

Al enviar, la corrección se reinyecta automáticamente en el pipeline y el procesamiento continúa desde donde se quedó.

### 14.2 Tipos de Tareas

**Clasificación:** el operador ve la imagen de la página y el texto OCR. Debe seleccionar el tipo documental correcto (factura, DNI, nómina, contrato, etc.).

**Extracción:** el operador ve el documento completo con los campos ya extraídos automáticamente. Los campos con confianza alta aparecen marcados como correctos. El operador solo debe completar o corregir los campos marcados como pendientes de revisión.

### 14.3 Gestión de Operadores

Cada operador tiene un perfil en la tabla `operators` con sus skills (qué tipos de tarea puede hacer), estado de actividad y tarea actual. Los skills y la disponibilidad se pueden gestionar directamente en la base de datos o mediante una interfaz de administración.

---

## 15. Guía de Desarrollo

### 15.1 Instalación Local

```bash
# Dependencias de producción
pip install -e .

# Con dependencias de desarrollo (tests, linting, formatting)
pip install -e ".[dev]"
```

### 15.2 Desarrollo contra Infraestructura en Docker

Para desarrollar un componente localmente ejecutándolo fuera de Docker pero contra RabbitMQ y PostgreSQL dockerizados:

```bash
# Levantar solo la infraestructura
docker compose up -d rabbitmq postgres

# Ejecutar migraciones
alembic upgrade head

# Ejecutar un componente individual
DOCPROC_COMPONENT_NAME=ocr python -m src

# O ejecutar el API Gateway con hot-reload
uvicorn src.components.api_gateway.app:app --reload --port 8000
```

### 15.3 Crear un Nuevo Componente

Para añadir un nuevo componente al pipeline se siguen cuatro pasos:

**Paso 1 — Crear el módulo.** Crear `src/components/mi_componente/component.py` con una clase que herede de `BaseComponent`. Se debe definir `component_name`, opcionalmente un método `setup()` para inicialización y obligatoriamente `process_message()` que recibe un `PipelineMessage` y una `AsyncSession` de SQLAlchemy, y devuelve una lista de tuplas `(sentinel_o_routing_key, PipelineMessage)`. Se usan sentinelas para enrutamiento dinámico: `"__next__"` para avanzar a la siguiente etapa del workflow y `"__backoffice__"` para derivar a intervención humana.

```python
from src.core.base_component import BaseComponent
from src.core.schemas import PipelineMessage
from sqlalchemy.ext.asyncio import AsyncSession

class MiComponente(BaseComponent):
    component_name = "mi_componente"

    async def setup(self):
        pass  # Cargar modelos, abrir conexiones, etc.

    async def process_message(
        self, message: PipelineMessage, session: AsyncSession
    ) -> list[tuple[str, PipelineMessage]]:
        resultado = message.model_copy(update={
            "source_component": self.component_name,
            "payload": {**message.payload, "nuevo_dato": "valor"},
        })
        return [("__next__", resultado)]  # El framework resuelve la siguiente etapa del workflow
```

**Paso 2 — Registrar en el dispatcher.** En `src/__main__.py`, añadir la entrada al diccionario `COMPONENT_REGISTRY`:

```python
COMPONENT_REGISTRY = {
    ...
    "mi_componente": "src.components.mi_componente.component.MiComponente",
}
```

**Paso 3 — Declarar la cola (si es nueva).** En `src/core/rabbitmq.py`, añadir el binding:

```python
QUEUE_BINDINGS = {
    ...
    "q.mi_componente": ("doc.direct", "mi.routing.key"),
}
```

**Paso 4 — Añadir al docker-compose.** Agregar un nuevo servicio con la misma imagen y la variable `DOCPROC_COMPONENT_NAME` correspondiente.

### 15.4 Sustituir un Stub por una Implementación Real

Los componentes de OCR, Classifier y Extractor están implementados como stubs. Para poner el sistema en producción, se sustituye la lógica del método `process_message()` por la integración con el modelo ML o servicio real, manteniendo la misma interfaz de entrada/salida (recibe `PipelineMessage`, devuelve lista de tuplas con sentinelas `("__next__", msg)` o `("__backoffice__", msg)`). La arquitectura no cambia.

### 15.5 Tests

```bash
make test          # Ejecutar pytest
make lint          # Linter con ruff
make format        # Auto-formatear con ruff
```

La estructura de tests incluye: `tests/unit/` para tests unitarios de componentes individuales, `tests/integration/` para tests end-to-end del pipeline completo con stubs, y `tests/fixtures/` con datos de prueba.

---

## 16. Comandos de Referencia (Makefile)

| Comando | Descripción |
|---|---|
| `make up` | Levantar todos los servicios |
| `make down` | Parar todos los servicios |
| `make logs` | Ver logs de todos los servicios |
| `make logs-ocr` | Ver logs de un servicio concreto |
| `make clean` | Parar todo y borrar volúmenes |
| `make migrate-docker` | Ejecutar migraciones en Docker |
| `make test` | Ejecutar tests con pytest |
| `make lint` | Linter con ruff |
| `make format` | Auto-formatear código |

---

## 17. Verificación End-to-End

Para verificar que el sistema funciona correctamente de extremo a extremo:

1. `docker compose up --build -d` levanta RabbitMQ, PostgreSQL y todos los componentes.

2. `docker compose exec api-gateway alembic upgrade head` crea las tablas.

3. Enviar un documento de prueba:
```bash
curl -X POST http://localhost:8000/process \
  -F file=@test.pdf \
  -F metadata='{"client":"test"}' \
  -F channel=api \
  -F workflow=default
```
Debe devolver `{"request_id": "uuid", "status": "received"}`.

4. Consultar el estado con `curl http://localhost:8000/status/{request_id}` y observar cómo progresa por los estados hasta `completed`.

5. Si el clasificador o extractor genera baja confianza, abrir `http://localhost:8001/?operator=operador1`, ver la tarea, completarla y verificar que el request llega a `completed`.

6. Verificar las colas en RabbitMQ Management UI en `http://localhost:15672` (credenciales: `guest/guest`).

---

## 18. Resolución de Problemas

**El documento se queda en estado `processing` indefinidamente.** Verificar que todos los componentes del pipeline están arrancados (`docker compose ps`). Revisar las colas en RabbitMQ Management UI para identificar dónde se acumulan mensajes. Comprobar los logs del componente que debería estar consumiendo (`make logs-classifier`, etc.).

**Las tareas manuales no aparecen en el Back Office.** Verificar que el Back Office está arrancado en el puerto 8001. Comprobar que la cola `q.backoffice.classification` o `q.backoffice.extraction` tiene mensajes. Verificar que el operador tiene los skills necesarios en su perfil.

**SLA breached en todos los trabajos.** Los SLAs pueden ser demasiado agresivos para el entorno de desarrollo. Aumentar `DOCPROC_DEFAULT_SLA_SECONDS` o el campo `deadline_seconds` en el workflow YAML. Verificar que hay suficientes workers para las etapas paralelas.

**Mensajes en la Dead Letter Queue.** Verificar la cola `q.dead_letters` en RabbitMQ Management UI. Los mensajes llegan ahí por errores de procesamiento (excepción no controlada) o por expiración de TTL. Revisar los logs del componente correspondiente para diagnosticar el error.

**Error de conexión a RabbitMQ o PostgreSQL.** Verificar que los servicios de infraestructura están arrancados (`docker compose ps rabbitmq postgres`). Comprobar las variables de entorno `DOCPROC_RABBITMQ_URL` y `DOCPROC_DATABASE_URL`.

---

## Apéndice A — Estructura Completa de Ficheros

```
document-processing-system/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── alembic.ini
├── .env.example
├── .dockerignore
├── .gitignore
├── README.md
├── config/
│   ├── settings.py
│   ├── logging.py
│   └── workflows/
│       └── default.yaml
├── src/
│   ├── __init__.py
│   ├── __main__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── base_component.py
│   │   ├── schemas.py
│   │   ├── models.py
│   │   ├── rabbitmq.py
│   │   ├── database.py
│   │   ├── routing.py
│   │   ├── workflow_loader.py
│   │   ├── sla.py
│   │   └── health.py
│   ├── components/
│   │   ├── api_gateway/app.py
│   │   ├── workflow_router/component.py
│   │   ├── splitter/component.py
│   │   ├── ocr/component.py
│   │   ├── classifier/component.py
│   │   ├── classification_aggregator/component.py
│   │   ├── extractor/component.py
│   │   ├── extraction_aggregator/component.py
│   │   ├── consolidator/component.py
│   │   ├── sla_monitor/component.py
│   │   └── backoffice/
│   │       ├── app.py
│   │       └── templates/
│   │           ├── index.html
│   │           └── task.html
│   └── migrations/
│       └── versions/
│           ├── 001_initial_schema.py
│           └── 002_add_workflow_routing_to_backoffice_tasks.py
├── tests/
│   ├── conftest.py
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── k8s/
│   ├── base/
│   └── overlays/
│       ├── dev/
│       └── prod/
└── docs/
    └── components/
        ├── 00-indice.md
        ├── 01-core-framework.md
        ├── 02-api-gateway.md
        ├── 03-workflow-router.md
        ├── 04-splitter.md
        ├── 05-ocr.md
        ├── 06-classifier.md
        ├── 07-classification-aggregator.md
        ├── 08-extractor.md
        ├── 09-extraction-aggregator.md
        ├── 10-consolidator.md
        ├── 11-sla-monitor.md
        └── 12-backoffice.md
```

---

## Apéndice B — Glosario

| Término | Definición |
|---|---|
| **DAG** | Grafo Dirigido Acíclico. Modelo del flujo de procesamiento donde las etapas son nodos y las conexiones son aristas dirigidas sin ciclos. |
| **Fan-out** | Patrón donde un mensaje de entrada genera N mensajes de salida que se procesan en paralelo (ej: Splitter genera un mensaje por página). |
| **Fan-in** | Patrón inverso al fan-out: N mensajes paralelos convergen en un Aggregator que espera a todos antes de continuar. |
| **Aggregator** | Componente responsable de los puntos de sincronización (fan-in). Usa conteo atómico en PostgreSQL. |
| **SLA** | Service Level Agreement. Tiempo máximo permitido para completar el procesamiento de un trabajo. |
| **Deadline** | Timestamp absoluto calculado como `timestamp_entrada + SLA_seconds`. |
| **Sentinela** | Constante especial (`__next__`, `__backoffice__`) devuelta por los componentes en lugar de routing keys concretos. El framework resuelve el sentinela consultando el workflow YAML. |
| **Enrutamiento dinámico** | Mecanismo por el cual los componentes no hardcodean el destino de sus mensajes, sino que usan sentinelas que el framework resuelve en tiempo de ejecución a partir del workflow YAML. Permite reordenar o saltar etapas sin modificar código. |
| **`current_stage`** | Campo del `PipelineMessage` que indica la etapa actual del workflow. Se actualiza automáticamente al resolver el sentinela `__next__`. |
| **Stub** | Implementación provisional de un componente que devuelve datos simulados para permitir pruebas del flujo completo. |
| **Workflow** | Definición en YAML de un flujo de procesamiento documental: etapas, umbrales, SLAs y esquemas de extracción. El orden de las etapas define el flujo de enrutamiento dinámico. |
| **PipelineMessage** | Envelope Pydantic universal que viaja por todas las colas de RabbitMQ, conteniendo request_id, workflow_name, current_stage, payload y metadatos. |
| **BaseComponent** | Clase abstracta que todo componente del pipeline hereda. Proporciona conexión RabbitMQ, sesión de BD, logging, resolución de sentinelas y lifecycle. |
| **Back Office** | Subsistema para intervención humana cuando la confianza automática es insuficiente. |
| **Human-in-the-Loop** | Patrón de diseño donde un operador humano interviene en el flujo automatizado para corregir o completar información. |
| **DLQ** | Dead Letter Queue. Cola donde van los mensajes que no se pudieron procesar (errores o TTL expirado). |
| **HPA** | Horizontal Pod Autoscaler de Kubernetes. Escala automáticamente el número de réplicas de un deployment. |
| **Hot Standby** | Workers siempre listos para procesar, sin esperar al escalado dinámico. Necesario para SLAs agresivos. |
