"""RabbitMQ connection management and exchange/queue declaration."""

import aio_pika
import structlog

logger = structlog.get_logger()

# Exchange definitions
EXCHANGES = {
    "doc.direct": aio_pika.ExchangeType.DIRECT,
    "doc.backoffice": aio_pika.ExchangeType.DIRECT,
    "doc.dlx": aio_pika.ExchangeType.FANOUT,
}

# Queue -> (exchange, routing_key) bindings
QUEUE_BINDINGS: dict[str, tuple[str, str]] = {
    "q.workflow_router": ("doc.direct", "request.new"),
    "q.splitter": ("doc.direct", "request.split"),
    "q.ocr": ("doc.direct", "page.ocr"),
    "q.classifier": ("doc.direct", "page.classify"),
    "q.classification_aggregator": ("doc.direct", "page.classified"),
    "q.extractor": ("doc.direct", "doc.extract"),
    "q.extraction_aggregator": ("doc.direct", "doc.extracted"),
    "q.consolidator": ("doc.direct", "request.consolidate"),
    "q.backoffice.classification": ("doc.backoffice", "task.classification"),
    "q.backoffice.extraction": ("doc.backoffice", "task.extraction"),
    "q.dead_letters": ("doc.dlx", ""),
}

# Default queue arguments
DEFAULT_QUEUE_ARGS = {
    "x-dead-letter-exchange": "doc.dlx",
    "x-message-ttl": 300_000,  # 5 minutes
}


async def setup_rabbitmq_topology(channel: aio_pika.Channel) -> dict[str, aio_pika.Exchange]:
    """Declare all exchanges and queues for the pipeline.

    Returns a dict of exchange_name -> Exchange objects.
    """
    exchanges: dict[str, aio_pika.Exchange] = {}

    # Declare exchanges
    for name, exchange_type in EXCHANGES.items():
        exchange = await channel.declare_exchange(name, exchange_type, durable=True)
        exchanges[name] = exchange
        logger.info("exchange_declared", name=name, type=exchange_type.value)

    # Declare queues and bind them
    for queue_name, (exchange_name, routing_key) in QUEUE_BINDINGS.items():
        # Dead letter queue has no DLX of its own
        args = {} if queue_name == "q.dead_letters" else dict(DEFAULT_QUEUE_ARGS)

        queue = await channel.declare_queue(queue_name, durable=True, arguments=args)
        await queue.bind(exchanges[exchange_name], routing_key=routing_key)
        logger.info("queue_declared", queue=queue_name, exchange=exchange_name, routing_key=routing_key)

    return exchanges
