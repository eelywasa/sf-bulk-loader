# Salesforce Bulk Loader

## What This Is
A containerized application for orchestrating large-scale data loads into Salesforce
using the Bulk API 2.0. Python backend (FastAPI), SQLite database, React frontend,
Docker deployment.

## Spec
The full specification is in SPEC.md. Always refer to it for architectural decisions,
data model, API design, and build order.

## Tech Stack
- Backend: Python 3.12+, FastAPI, SQLAlchemy, Alembic, httpx
- Database: SQLite with WAL mode
- Frontend: React, Vite, TypeScript, Tailwind CSS
- Containerization: Docker, Docker Compose

## Project Structure
- backend/  — FastAPI application
- frontend/ — React application
- data/input/  — Source CSV files (mounted read-only in Docker)
- data/output/ — Success/error result logs
- data/db/     — SQLite database file

## Commands
- Backend dev server: `cd backend && uvicorn app.main:app --reload`
- Frontend dev server: `cd frontend && npm run dev`
- Run tests: `cd backend && pytest`
- Docker build: `docker compose up --build`
- DB migrations: `cd backend && alembic upgrade head`

## Code Standards
- Python: Use async/await throughout. Type hints on all function signatures.
- Use Pydantic for request/response schemas.
- Use SQLAlchemy 2.0 style (mapped_column, etc.).
- Frontend: Functional components with hooks. No class components.
- Tailwind for styling. No separate CSS files.

## Key Design Decisions
- JWT Bearer auth only (no OAuth web flow or username/password in MVP).
- External IDs for object relationships (no runtime ID mapping).
- SQLite (not Postgres) for simplicity — accessed via SQLAlchemy so swappable later.
- asyncio for background task processing (no Celery).
- CSV streaming with Python's csv module (not pandas).

## Testing
- Write tests alongside implementation, not as a separate pass.
- Use pytest with async support (pytest-asyncio).
- Use FastAPI TestClient for API endpoint tests.

## Important
- Never commit .env files or credentials.
- Encryption key for stored secrets comes from ENCRYPTION_KEY env var.
- Salesforce API version is configurable (default v62.0).