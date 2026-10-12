from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Task, User
from schemas import TaskCreate, TaskUpdate


def create_user(db: Session, email: str) -> User:
    user = User(email=email)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_tasks(db: Session, owner_id: int, *, done: bool | None = None) -> list[Task]:
    stmt = select(Task).where(Task.owner_id == owner_id)
    if done is not None:
        stmt = stmt.where(Task.done == done)
    return list(db.scalars(stmt.order_by(Task.id)))


def create_task(db: Session, payload: TaskCreate) -> Task:
    task = Task(title=payload.title, owner_id=payload.owner_id)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(db: Session, task_id: int, payload: TaskUpdate) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, task_id: int) -> bool:
    task = db.get(Task, task_id)
    if task is None:
        return False
    db.delete(task)
    db.commit()
    return True
