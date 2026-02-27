"""Abstract base class for all pipeline components."""

import abc
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

import aio_pika
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import setup_logging
from config.settings import Settings
from src.core.database import create_db_engine, create_session_factory
from src.core.health import HealthServer
from src.core.rabbitmq import setup_rabbitmq_topology
from src.core.routing import resolve_routing
from src.core.schemas import PipelineMessage
from src.core.workflow_loader import WorkflowLoader


class BaseComponent(abc.ABC):
    """Abstract base for all pipeline components.

    Subclasses MUST implement:
        - component_name (property): unique string identifier
        - process_message(message, session): the business logic

    Subclasses MAY override:
        - input_queue (property): defaults to f"q.{component_name}"
        - setup(): one-time initialization
        - teardown(): cleanup
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        setup_logging()
        self.logger = structlog.get_logger().bind(component=self.component_name)
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.Channel] = None
        self._exchanges: dict[str, aio_pika.Exchange] = {}
        self._db_engine = create_db_engine(settings)
        self._session_factory = create_session_factory(self._db_engine)
        self._health_server = HealthServer(port=settings.health_port)
        self._workflow_loader = WorkflowLoader(settings.workflows_dir)
        self._shutdown_event = asyncio.Event()

    @property
    @abc.abstractmethod
    def component_name(self) -> str:
        """Unique name like 'ocr', 'classifier', 'splitter'."""
        ...

    @property
    def input_queue(self) -> str:
        """Queue to consume from. Override if not standard."""
        return f"q.{self.component_name}"

    @abc.abstractmethod
    async def process_message(
        self,
        message: PipelineMessage,
        session: AsyncSession,
    ) -> list[tuple[str, PipelineMessage]]:
        """Core business logic.

        Args:
            message: Deserialized incoming message.
            session: Database session (auto-committed on success, rolled back on error).

        Returns:
            List of (routing_key, outgoing_message) tuples to publish.
            Return empty list if no downstream messages needed.
        """
        ...

    async def run(self) -> None:
        """Main entry point. Sets up connections and starts consuming."""
        self._register_signals()
        await self._health_server.start()
        await self.setup()

        self._connection = await aio_pika.connect_robust(self.settings.rabbitmq_url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self.settings.prefetch_count)

        # Declare full topology (idempotent)
        self._exchanges = await setup_rabbitmq_topology(self._channel)

        # Start consuming from our input queue
        queue = await self._channel.get_queue(self.input_queue)
        self.logger.info("consuming", queue=self.input_queue)
        self._health_server.set_ready(True)
        await queue.consume(self._on_message)

        # Wait until shutdown signal
        await self._shutdown_event.wait()
        self.logger.info("shutting_down")
        await self.teardown()

    async def _on_message(self, raw_message: aio_pika.IncomingMessage) -> None:
        """Deserialize, open DB session, call process_message, publish results, ack/nack."""
        async with raw_message.process(requeue=True):
            message = PipelineMessage.model_validate_json(raw_message.body)
            self.logger.info(
                "message_received",
                request_id=str(message.request_id),
                trace_id=str(message.trace_id),
            )

            start = datetime.now(timezone.utc)
            async with self._session_factory() as session:
                async with session.begin():
                    outgoing = await self.process_message(message, session)

            # Publish all outgoing messages AFTER successful DB commit
            published = 0
            for routing_key, out_msg in outgoing:
                resolved = resolve_routing(
                    routing_key, out_msg, self._workflow_loader, self.component_name,
                )
                if resolved is None:
                    self.logger.debug(
                        "terminal_stage_no_publish",
                        request_id=str(out_msg.request_id),
                    )
                    continue
                exchange_name, actual_key, updated_msg = resolved
                await self._publish(exchange_name, actual_key, updated_msg)
                published += 1

            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            self.logger.info(
                "message_processed",
                request_id=str(message.request_id),
                elapsed_s=round(elapsed, 3),
                published_count=published,
            )

    async def _publish(self, exchange_name: str, routing_key: str, message: PipelineMessage) -> None:
        """Publish a message to a named exchange."""
        exchange = self._exchanges[exchange_name]
        await exchange.publish(
            aio_pika.Message(
                body=message.model_dump_json().encode(),
                content_type="application/json",
                message_id=str(uuid.uuid4()),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                headers={
                    "request_id": str(message.request_id),
                    "component": self.component_name,
                },
            ),
            routing_key=routing_key,
        )
        self.logger.debug(
            "message_published",
            exchange=exchange_name,
            routing_key=routing_key,
            request_id=str(message.request_id),
        )

    async def publish_to_backoffice(self, routing_key: str, message: PipelineMessage) -> None:
        """Publish to the backoffice exchange."""
        await self._publish("doc.backoffice", routing_key, message)

    async def setup(self) -> None:
        """Override for one-time initialization (load models, warm caches)."""

    async def teardown(self) -> None:
        """Cleanup connections."""
        self._health_server.set_ready(False)
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
        await self._db_engine.dispose()
        await self._health_server.stop()

    def _register_signals(self) -> None:
        """Graceful shutdown on SIGTERM/SIGINT."""
        loop = asyncio.get_event_loop()
        for sig_name in ("SIGTERM", "SIGINT"):
            import signal

            sig = getattr(signal, sig_name, None)
            if sig:
                loop.add_signal_handler(sig, self._shutdown_event.set)
