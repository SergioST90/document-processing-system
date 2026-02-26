"""API Gateway: FastAPI application for receiving processing requests."""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import structlog
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from config.logging import setup_logging
from config.settings import Settings
from src.core.database import create_db_engine, create_session_factory
from src.core.models import Request
from src.core.rabbitmq import setup_rabbitmq_topology
from src.core.schemas import JobStatusResponse, PipelineMessage, ProcessResponse

logger = structlog.get_logger()
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle management."""
    setup_logging()

    # Database
    engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(engine)

    # RabbitMQ
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    app.state.exchanges = await setup_rabbitmq_topology(channel)
    app.state.rmq_channel = channel
    app.state.rmq_connection = connection

    # Storage directory
    Path(settings.storage_path).mkdir(parents=True, exist_ok=True)

    logger.info("api_gateway_started")
    yield

    # Cleanup
    await connection.close()
    await engine.dispose()
    logger.info("api_gateway_stopped")


app = FastAPI(title="DocProc API Gateway", version="0.1.0", lifespan=lifespan)


@app.post("/process", response_model=ProcessResponse)
async def process_document(
    file: UploadFile = File(...),
    metadata: str = Form(default="{}"),
    channel: str = Form(default="api"),
    workflow: str = Form(default="default"),
    external_id: str | None = Form(default=None),
):
    """Receive a document for processing.

    Creates a job, stores the file, and publishes to the pipeline.
    """
    import json

    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in metadata field")

    request_id = uuid.uuid4()

    # Store the uploaded file
    storage_dir = Path(settings.storage_path) / str(request_id)
    storage_dir.mkdir(parents=True, exist_ok=True)
    file_path = storage_dir / (file.filename or "uploaded_file")
    content = await file.read()
    file_path.write_bytes(content)

    # Create DB record
    async with app.state.session_factory() as session:
        async with session.begin():
            request = Request(
                id=request_id,
                external_id=external_id,
                channel=channel,
                workflow_name=workflow,
                status="received",
                original_filename=file.filename,
                file_storage_path=str(file_path),
                metadata_=meta,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            session.add(request)

    # Publish to pipeline
    message = PipelineMessage(
        request_id=request_id,
        workflow_name=workflow,
        payload={
            "channel": channel,
            "file_path": str(file_path),
            "original_filename": file.filename,
            "metadata": meta,
        },
        source_component="api_gateway",
    )

    exchange = app.state.exchanges["doc.direct"]
    await exchange.publish(
        aio_pika.Message(
            body=message.model_dump_json().encode(),
            content_type="application/json",
            message_id=str(uuid.uuid4()),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key="request.new",
    )

    logger.info("request_created", request_id=str(request_id), workflow=workflow, channel=channel)
    return ProcessResponse(request_id=request_id, status="received")


@app.get("/status/{request_id}", response_model=JobStatusResponse)
async def get_status(request_id: uuid.UUID):
    """Get the current processing status of a request."""
    from sqlalchemy import select

    async with app.state.session_factory() as session:
        result = await session.execute(select(Request).where(Request.id == request_id))
        request = result.scalar_one_or_none()

    if not request:
        raise HTTPException(status_code=404, detail="Request not found")

    return JobStatusResponse(
        request_id=request.id,
        status=request.status,
        workflow_name=request.workflow_name,
        created_at=request.created_at,
        deadline_utc=request.deadline_utc,
        completed_at=request.completed_at,
        page_count=request.page_count,
        document_count=request.document_count,
        result=request.result_payload,
        error=request.error_message,
    )


@app.get("/health")
async def health():
    return {"status": "ok"}
