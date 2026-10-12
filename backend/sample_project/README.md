# Task API — Sample Codebase

Small FastAPI + SQLAlchemy todo service used by CodeMap AI's "Load Sample" demo.

## Layout

- `main.py` — FastAPI app, router wiring, startup table creation
- `database.py` — SQLAlchemy engine, session factory, `get_db`
- `models.py` — ORM models
- `schemas.py` — Pydantic request/response models
- `crud.py` — data-access helpers
- `routers/todos.py` — HTTP endpoints

## Run

```bash
pip install fastapi sqlalchemy pydantic uvicorn
uvicorn main:app --reload
```
