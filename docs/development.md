# Local Development

---

## Backend

```bash
cd backend

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp ../.env.example .env
# Set ENCRYPTION_KEY, JWT_SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD

# Apply database migrations
alembic upgrade head

# Start the development server (auto-reloads on file changes)
uvicorn app.main:app --reload
```

Backend: http://localhost:8000
API docs (Swagger): http://localhost:8000/docs

---

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend: http://localhost:5173
API calls are proxied to `http://localhost:8000` via the Vite dev server config.

Set `APP_ENV=development` in the backend `.env` to allow the Vite dev origin through CORS.

---

## Database Migrations

```bash
cd backend

# Apply all pending migrations
alembic upgrade head

# Create a new migration after changing SQLAlchemy models
alembic revision --autogenerate -m "describe your change"
```

---

## Running Tests

### Backend

```bash
cd backend
pytest          # all tests
pytest -v       # verbose output
pytest tests/test_csv_processor.py   # single file
pytest -k test_create_plan           # by name pattern
```

Tests use a file-based SQLite database (`backend/test_api.db`, cleaned up after the
run). No Salesforce connection is required.

**Against PostgreSQL:**

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/test_db \
  pytest -x -q
```

### Frontend

```bash
cd frontend
npm test            # watch mode
npm run test:run    # single run (CI)
npm run typecheck   # TypeScript type check
```
