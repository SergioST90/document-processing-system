"""Back Office: FastAPI application for human operators to review and complete tasks."""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aio_pika
import structlog
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from config.logging import setup_logging
from config.settings import Settings
from src.core.database import create_db_engine, create_session_factory
from src.core.models import BackofficeTask, Operator, Page, Document
from src.core.rabbitmq import setup_rabbitmq_topology
from src.core.schemas import PipelineMessage

logger = structlog.get_logger()
settings = Settings()

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()

    engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(engine)

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    channel = await connection.channel()
    app.state.exchanges = await setup_rabbitmq_topology(channel)
    app.state.rmq_channel = channel
    app.state.rmq_connection = connection

    logger.info("backoffice_started")
    yield

    await connection.close()
    await engine.dispose()


app = FastAPI(title="DocProc Back Office", version="0.1.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, operator: str = "default_operator"):
    """Main operator dashboard showing pending tasks."""
    async with app.state.session_factory() as session:
        result = await session.execute(
            select(BackofficeTask)
            .where(BackofficeTask.status.in_(["pending", "assigned"]))
            .order_by(BackofficeTask.priority, BackofficeTask.created_at)
        )
        tasks = list(result.scalars().all())

    return templates.TemplateResponse("index.html", {
        "request": request,
        "tasks": tasks,
        "operator": operator,
    })


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
async def task_detail(request: Request, task_id: uuid.UUID, operator: str = "default_operator"):
    """Task detail page where operator reviews and submits corrections."""
    async with app.state.session_factory() as session:
        result = await session.execute(select(BackofficeTask).where(BackofficeTask.id == task_id))
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return templates.TemplateResponse("task.html", {
        "request": request,
        "task": task,
        "operator": operator,
    })


@app.post("/tasks/{task_id}/claim")
async def claim_task(task_id: uuid.UUID, operator: str = Form(...)):
    """Operator claims a pending task."""
    async with app.state.session_factory() as session:
        async with session.begin():
            result = await session.execute(select(BackofficeTask).where(BackofficeTask.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")
            if task.status != "pending":
                raise HTTPException(status_code=409, detail=f"Task already {task.status}")

            task.status = "assigned"
            task.assigned_to = operator
            task.assigned_at = datetime.now(timezone.utc)

    logger.info("task_claimed", task_id=str(task_id), operator=operator)
    return RedirectResponse(url=f"/tasks/{task_id}?operator={operator}", status_code=303)


@app.post("/tasks/{task_id}/submit")
async def submit_task(
    task_id: uuid.UUID,
    operator: str = Form(...),
    doc_type: str = Form(default=""),
    extracted_data: str = Form(default="{}"),
):
    """Operator submits task result. Reinserts the result into the pipeline."""
    import json

    try:
        output_data = json.loads(extracted_data) if extracted_data != "{}" else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in extracted_data")

    async with app.state.session_factory() as session:
        async with session.begin():
            result = await session.execute(select(BackofficeTask).where(BackofficeTask.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            task.status = "completed"
            task.completed_at = datetime.now(timezone.utc)

            if task.task_type == "classification":
                # Update the page with the operator's classification
                final_type = doc_type or task.input_data.get("suggested_type", "unknown")
                task.output_data = {"doc_type": final_type, "operator": operator}

                page_result = await session.execute(
                    select(Page).where(Page.id == task.reference_id)
                )
                page = page_result.scalar_one()
                page.doc_type = final_type
                page.classification_confidence = 1.0  # Manual = 100% confidence
                page.status = "classified"
                page.updated_at = datetime.now(timezone.utc)

                # Publish back to pipeline: page.classified
                message = PipelineMessage(
                    request_id=task.request_id,
                    page_index=page.page_index,
                    source_component="backoffice",
                    payload={
                        "page_id": str(page.id),
                        "page_index": page.page_index,
                        "doc_type": final_type,
                        "classification_confidence": 1.0,
                        "origin": "backoffice",
                    },
                )
                exchange = app.state.exchanges["doc.direct"]
                await exchange.publish(
                    aio_pika.Message(
                        body=message.model_dump_json().encode(),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key="page.classified",
                )

            elif task.task_type == "extraction":
                # Update document with operator's extraction
                task.output_data = {"extracted_data": output_data, "operator": operator}

                doc_result = await session.execute(
                    select(Document).where(Document.id == task.reference_id)
                )
                doc = doc_result.scalar_one()
                merged = {**(doc.extracted_data or {}), **output_data}
                doc.extracted_data = merged
                doc.extraction_confidence = 1.0
                doc.status = "extracted"
                doc.updated_at = datetime.now(timezone.utc)

                # Publish back to pipeline: doc.extracted
                message = PipelineMessage(
                    request_id=task.request_id,
                    document_id=doc.id,
                    source_component="backoffice",
                    payload={
                        "document_id": str(doc.id),
                        "doc_type": doc.doc_type,
                        "extracted_data": merged,
                        "extraction_confidence": 1.0,
                        "origin": "backoffice",
                    },
                )
                exchange = app.state.exchanges["doc.direct"]
                await exchange.publish(
                    aio_pika.Message(
                        body=message.model_dump_json().encode(),
                        content_type="application/json",
                        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    ),
                    routing_key="doc.extracted",
                )

    logger.info("task_submitted", task_id=str(task_id), task_type=task.task_type, operator=operator)
    return RedirectResponse(url=f"/?operator={operator}", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok"}


# --- API Endpoints for programmatic access ---

@app.get("/api/tasks")
async def api_list_tasks(status: str = "pending", skill: str | None = None):
    """List tasks, optionally filtered by status and skill."""
    async with app.state.session_factory() as session:
        query = select(BackofficeTask).where(BackofficeTask.status == status)
        if skill:
            query = query.where(BackofficeTask.required_skills.contains([skill]))
        query = query.order_by(BackofficeTask.priority, BackofficeTask.created_at)
        result = await session.execute(query)
        tasks = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "request_id": str(t.request_id),
            "task_type": t.task_type,
            "status": t.status,
            "priority": t.priority,
            "assigned_to": t.assigned_to,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "input_data": t.input_data,
        }
        for t in tasks
    ]
