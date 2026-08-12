from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import LLMAuditLog
from app.schemas import AuditOut

router = APIRouter(tags=["audit"])


@router.get("/audit/llm-calls", response_model=list[AuditOut])
def llm_calls(limit: int = 50, db: Session = Depends(get_db)):
    """Every payload that left the machine, exactly as it left. Queryable table,
    not a log file — this is the privacy claim, auditable live."""
    return (
        db.execute(select(LLMAuditLog).order_by(LLMAuditLog.id.desc()).limit(limit))
        .scalars()
        .all()
    )
