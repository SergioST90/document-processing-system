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

## Flujo completo del mensaje

```
POST /process --> [API Gateway] --> q.workflow_router
    --> [Workflow Router] --> q.splitter
    --> [Splitter] --> fan-out N paginas --> q.ocr
    --> [OCR] (x N) --> q.classifier
    --> [Classifier] (x N)
        |-- confianza alta --> q.classification_aggregator
        |-- confianza baja --> q.backoffice.classification --> [Back Office] --> q.classification_aggregator
    --> [Classification Aggregator] (fan-in) --> fan-out M documentos --> q.extractor
    --> [Extractor] (x M)
        |-- confianza alta --> q.extraction_aggregator
        |-- confianza baja --> q.backoffice.extraction --> [Back Office] --> q.extraction_aggregator
    --> [Extraction Aggregator] (fan-in) --> q.consolidator
    --> [Consolidator] --> COMPLETADO
```
