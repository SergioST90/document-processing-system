# Documentacion de Componentes - DocProc

## Indice

### Core Framework
- [01 - Core Framework](01-core-framework.md) - BaseComponent, PipelineMessage, topologia RabbitMQ, modelos de BD

### Componentes del Pipeline
- [02 - API Gateway](02-api-gateway.md) - Punto de entrada HTTP para recibir peticiones
- [03 - Workflow Router](03-workflow-router.md) - Seleccion de flujo y calculo de SLA
- [04 - Splitter](04-splitter.md) - Descompresion de ficheros y fan-out por pagina
- [05 - OCR](05-ocr.md) - Extraccion de texto por pagina
- [06 - Classifier](06-classifier.md) - Clasificacion de tipo documental con routing por confianza
- [07 - Classification Aggregator](07-classification-aggregator.md) - Fan-in de paginas y agrupacion en documentos
- [08 - Extractor](08-extractor.md) - Extraccion de datos estructurados por documento
- [09 - Extraction Aggregator](09-extraction-aggregator.md) - Fan-in de documentos extraidos
- [10 - Consolidator](10-consolidator.md) - Ensamblaje del resultado final
- [11 - SLA Monitor](11-sla-monitor.md) - Vigilancia de deadlines y escalado
- [12 - Back Office](12-backoffice.md) - Interfaz web para operadores humanos

## Flujo completo del mensaje (workflow default)

```
POST /process --> [API Gateway] --> q.workflow_router
    --> [Workflow Router] --> primera etapa del workflow YAML
    --> [Splitter] --> fan-out N paginas --> __next__ (siguiente etapa del YAML)
    --> [OCR] (x N) --> __next__
    --> [Classifier] (x N)
        |-- confianza alta --> __next__ (siguiente etapa del YAML)
        |-- confianza baja --> __backoffice__ (backoffice_queue del YAML) --> [Back Office] --> siguiente etapa
    --> [Classification Aggregator] (fan-in) --> fan-out M documentos --> __next__
    --> [Extractor] (x M)
        |-- confianza alta --> __next__
        |-- confianza baja --> __backoffice__ --> [Back Office] --> siguiente etapa
    --> [Extraction Aggregator] (fan-in) --> __next__
    --> [Consolidator] --> COMPLETADO (etapa terminal, no hay __next__)
```

El enrutamiento es dinamico: cada componente emite `__next__` y el framework resuelve la siguiente etapa consultando el workflow YAML. Crear un YAML diferente cambia el orden del pipeline sin tocar codigo.
