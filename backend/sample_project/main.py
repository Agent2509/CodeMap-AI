from __future__ import annotations

from fastapi import FastAPI

from database import Base, engine
from routers import todos

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Task API", version="0.1.0")
app.include_router(todos.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
