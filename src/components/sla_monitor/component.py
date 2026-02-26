"""SLA Monitor: polls for approaching deadlines and escalates.

This is NOT a standard BaseComponent queue consumer. It runs a periodic polling loop.
"""

import asyncio
from datetime import datetime, timezone

import aio_pika
import structlog
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.logging import setup_logging
from config.settings import Settings
from src.core.database import create_db_engine, create_session_factory
from src.core.health import HealthServer
from src.core.models import Request
from src.core.rabbitmq import setup_rabbitmq_topology


class SLAMonitorComponent:
    """Periodically checks for requests approaching or exceeding their SLA deadline."""

    component_name = "sla_monitor"

    def __init__(self, settings: Settings):
        self.settings = settings
        setup_logging()
        self.logger = structlog.get_logger().bind(component=self.component_name)
        self._db_engine = create_db_engine(settings)
        self._session_factory = create_session_factory(self._db_engine)
        self._health_server = HealthServer(port=settings.health_port)
        self._shutdown = False

    async def run(self) -> None:
        await self._health_server.start()
        self._health_server.set_ready(True)
        self.logger.info("sla_monitor_started", poll_interval=5)

        try:
            while not self._shutdown:
                await self._check_deadlines()
                await asyncio.sleep(5)
        finally:
            await self._health_server.stop()
            await self._db_engine.dispose()

    async def _check_deadlines(self) -> None:
        """Find requests that are at risk or have breached their SLA."""
        async with self._session_factory() as session:
            now = datetime.now(timezone.utc)

            # Find active requests with approaching deadlines (within 30% of remaining time)
            result = await session.execute(
                select(Request).where(
                    Request.status.notin_(["completed", "failed", "sla_breached"]),
                    Request.deadline_utc.isnot(None),
                    Request.deadline_utc <= now,
                )
            )
            breached = list(result.scalars().all())

            for request in breached:
                async with session.begin():
                    request.status = "sla_breached"
                    request.error_message = f"SLA breached at {now.isoformat()}"
                    request.updated_at = now
                    self.logger.warning(
                        "sla_breached",
                        request_id=str(request.id),
                        deadline=request.deadline_utc.isoformat() if request.deadline_utc else None,
                        sla_seconds=request.sla_seconds,
                    )

            # Log at-risk requests (within 30% of deadline remaining)
            at_risk_result = await session.execute(
                text("""
                    SELECT id, deadline_utc, status,
                        EXTRACT(EPOCH FROM (deadline_utc - NOW())) as remaining_seconds
                    FROM requests
                    WHERE status NOT IN ('completed', 'failed', 'sla_breached')
                        AND deadline_utc IS NOT NULL
                        AND deadline_utc > NOW()
                        AND EXTRACT(EPOCH FROM (deadline_utc - NOW())) < (sla_seconds * 0.3)
                """)
            )
            for row in at_risk_result.fetchall():
                self.logger.warning(
                    "sla_at_risk",
                    request_id=str(row[0]),
                    remaining_seconds=round(row[3], 1),
                    status=row[2],
                )
