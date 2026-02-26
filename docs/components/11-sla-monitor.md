# SLA Monitor

## Que hace

Es un proceso de vigilancia que se ejecuta en un bucle continuo, comprobando periodicamente si hay trabajos que estan cerca de o han superado su deadline de SLA. Cuando detecta un incumplimiento, marca el trabajo como `"sla_breached"`. Cuando detecta trabajos en riesgo, emite warnings en los logs.

A diferencia del resto de componentes del pipeline, **no consume de ninguna cola de RabbitMQ**. Es un proceso de polling que consulta la base de datos cada 5 segundos.

**Fichero**: `src/components/sla_monitor/component.py`

## Como se utiliza

### En el pipeline

Se ejecuta como un servicio independiente. No participa en el flujo de mensajes sino que vigila el estado global del sistema.

### Variable de entorno

```bash
DOCPROC_COMPONENT_NAME=sla_monitor
```

### Interpretar sus logs

El SLA Monitor emite dos tipos de alertas en los logs:

**SLA incumplido** (nivel WARNING):
```json
{
  "event": "sla_breached",
  "request_id": "uuid",
  "deadline": "2024-01-15T10:31:00Z",
  "sla_seconds": 60,
  "component": "sla_monitor"
}
```

**SLA en riesgo** (nivel WARNING):
```json
{
  "event": "sla_at_risk",
  "request_id": "uuid",
  "remaining_seconds": 8.3,
  "status": "extracting",
  "component": "sla_monitor"
}
```

Estos logs son los que un sistema de monitorizacion (Grafana, ELK, Datadog) deberia capturar para generar alertas.

## Como esta implementado

### Tipo de componente

**No** hereda de `BaseComponent`. Es una clase independiente con su propio ciclo de vida, ya que no necesita consumir de una cola RabbitMQ. Tiene su propio engine de BD, health server y bucle principal.

### Metodo `run()`

1. Arranca el health server HTTP
2. Marca el componente como ready
3. Entra en un bucle infinito:
   - Llama a `_check_deadlines()`
   - Espera 5 segundos (`asyncio.sleep(5)`)
4. Al apagar: cierra health server y engine de BD

### Metodo `_check_deadlines()`

Se ejecuta cada 5 segundos y hace dos comprobaciones:

**1. Deteccion de SLA incumplidos:**

Busca en BD todos los requests donde:
- `status` no es `completed`, `failed` ni `sla_breached`
- `deadline_utc` no es null
- `deadline_utc <= NOW()` (el deadline ya paso)

Para cada uno:
- Actualiza `status` a `"sla_breached"`
- Actualiza `error_message` con el timestamp del incumplimiento
- Emite un log WARNING

**2. Deteccion de SLA en riesgo:**

Ejecuta una query SQL directa que busca requests donde:
- El status es activo (no completado ni fallido)
- Tienen deadline establecido
- El deadline aun no ha pasado
- El tiempo restante es menor al 30% del SLA total

Para cada uno, emite un log WARNING con los segundos restantes y el estado actual.

### Query SQL para deteccion de riesgo

```sql
SELECT id, deadline_utc, status,
    EXTRACT(EPOCH FROM (deadline_utc - NOW())) as remaining_seconds
FROM requests
WHERE status NOT IN ('completed', 'failed', 'sla_breached')
    AND deadline_utc IS NOT NULL
    AND deadline_utc > NOW()
    AND EXTRACT(EPOCH FROM (deadline_utc - NOW())) < (sla_seconds * 0.3)
```

Por ejemplo, para un SLA de 60 segundos, el umbral de riesgo es cuando quedan menos de 18 segundos (30% de 60).

### Extensiones futuras

El SLA Monitor es el punto natural para anadir:

- **Escalado de prioridad**: Aumentar la `priority` de los `backoffice_tasks` asociados a requests en riesgo para que los operadores los vean primero.
- **Respuesta parcial anticipada**: Cuando un request esta en riesgo, el monitor podria invocar al Consolidator prematuramente para emitir una respuesta parcial con los documentos ya procesados.
- **Alertas externas**: Enviar notificaciones a Slack, email, PagerDuty cuando hay incumplimientos.
- **Auto-escalado reactivo**: Publicar metricas que el HPA de Kubernetes use para escalar los componentes que esten generando cuello de botella.
- **Escalado a manual**: Redirigir automaticamente a back office las tareas automaticas que estan tardando demasiado.
