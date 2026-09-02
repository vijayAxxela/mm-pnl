# routes/ui_state.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Any
from database.db import get_db, UiState

router = APIRouter()


class UiStateValue(BaseModel):
    value: Any


@router.get("/{key}")
def get_ui_state(key: str, db: Session = Depends(get_db)):
    """Read back previously-saved UI state (e.g. PNL tree expanded nodes/filters). None if never saved."""
    row = db.query(UiState).filter(UiState.key == key).first()
    return {"value": row.value if row else None}


@router.put("/{key}")
def set_ui_state(key: str, payload: UiStateValue, db: Session = Depends(get_db)):
    """Upsert UI state for a given key. Shared globally — this app has no per-user accounts."""
    row = db.query(UiState).filter(UiState.key == key).first()
    if row:
        row.value = payload.value
    else:
        row = UiState(key=key, value=payload.value)
        db.add(row)
    db.commit()
    return {"status": "ok"}
