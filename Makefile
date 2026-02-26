.PHONY: install dev up down logs build migrate test lint clean

# Install production dependencies
install:
	pip install -e .

# Install with dev dependencies
dev:
	pip install -e ".[dev]"

# Start all services with docker-compose
up:
	docker compose up --build -d

# Stop all services
down:
	docker compose down

# Follow logs for all services
logs:
	docker compose logs -f

# Follow logs for a specific service
logs-%:
	docker compose logs -f $*

# Build Docker image
build:
	docker compose build

# Run Alembic migrations
migrate:
	alembic upgrade head

# Run Alembic migration in Docker
migrate-docker:
	docker compose exec api-gateway alembic upgrade head

# Run tests
test:
	pytest tests/ -v

# Run linter
lint:
	ruff check src/ tests/
	ruff format --check src/ tests/

# Format code
format:
	ruff check --fix src/ tests/
	ruff format src/ tests/

# Clean up
clean:
	docker compose down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
