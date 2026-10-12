from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from crud import create_task, delete_task, list_tasks, update_task
from database import get_db
from schemas import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskRead])
def read_tasks(
    owner_id: int = Query(...),
    done: bool | None = None,
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    return list_tasks(db, owner_id, done=done)


@router.post("", response_model=TaskRead, status_code=201)
def add_task(payload: TaskCreate, db: Session = Depends(get_db)) -> TaskRead:
    return create_task(db, payload)


@router.patch("/{task_id}", response_model=TaskRead)
def edit_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db)) -> TaskRead:
    task = update_task(db, task_id, payload)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.delete("/{task_id}", status_code=204)
def remove_task(task_id: int, db: Session = Depends(get_db)) -> None:
    if not delete_task(db, task_id):
        raise HTTPException(status_code=404, detail="task not found")
