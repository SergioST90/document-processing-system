from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Component identification
    component_name: str = "unknown"

    # RabbitMQ
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    prefetch_count: int = 1
    message_ttl_ms: int = 300_000  # 5 minutes

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://docproc:docproc@localhost:5432/docproc"

    # Health check
    health_port: int = 8080

    # SLA defaults
    default_sla_seconds: int = 60

    # Confidence thresholds
    classification_confidence_threshold: float = 0.80
    extraction_confidence_threshold: float = 0.75

    # Back office
    backoffice_task_timeout_seconds: int = 120

    # File storage (local volume for MVP)
    storage_path: str = "/tmp/docproc/storage"

    # Workflow config directory
    workflows_dir: str = "config/workflows"

    model_config = {"env_prefix": "DOCPROC_", "env_file": ".env"}
