from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import narrate
from app.db import DEMO_USER_ID, get_db
from app.models import Category, Transaction
from app.schemas import CategoryOut, TransactionIn, TransactionOut, TransactionResult
from app.services import alerts as alert_svc
from app.services import budgets as budget_svc
from app.services.spending import month_bounds

router = APIRouter(tags=["transactions"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return db.execute(select(Category).order_by(Category.name)).scalars().all()


@router.post("/transactions", response_model=TransactionResult)
def add_transaction(payload: TransactionIn, db: Session = Depends(get_db)):
    """Insert, evaluate alerts and return both in one round trip — the UI must
    not need a second call to know it just blew the budget."""
    if not db.get(Category, payload.category_id):
        raise HTTPException(404, "unknown category")

    txn = Transaction(
        user_id=DEMO_USER_ID,
        category_id=payload.category_id,
        amount=round(payload.amount, 2),
        txn_type=payload.txn_type,
        note=payload.note,
        txn_date=payload.txn_date or date.today(),
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)

    alert = alert_svc.evaluate(db, txn)
    reallocation = None
    if alert:
        budget = budget_svc.find_active(db, DEMO_USER_ID, txn.category_id, txn.txn_date)
        state = budget_svc.status(db, budget, as_of=txn.txn_date)
        # Amounts are handed over pre-formatted. The model is not asked to round
        # or punctuate a figure — it copies strings. Deterministic sentence goes
        # in as the fallback, so a failure costs charm, never a correct number.
        facts = {
            "level": alert.level,
            "category": txn.category.name,
            "spent": f"₹{alert.spent_at_trigger:,.0f}",
            "limit": f"₹{alert.limit_at_trigger:,.0f}",
            "projected_total": f"₹{alert.projected_at_trigger:,.0f}",
            "pct_used": f"{state['pct_used']:.0f}%",
            "days_left": state["days_left"],
        }
        alert.message = narrate.alert(db, facts, alert.message)
        db.commit()

        reallocation = alert_svc.reallocation(db, DEMO_USER_ID, budget, on=txn.txn_date)

    return TransactionResult(transaction=txn, alert=alert, reallocation=reallocation)


@router.get("/transactions", response_model=list[TransactionOut])
def list_transactions(month: str | None = None, db: Session = Depends(get_db)):
    start, end = month_bounds(month)
    return (
        db.execute(
            select(Transaction)
            .where(
                Transaction.user_id == DEMO_USER_ID,
                Transaction.txn_date.between(start, end),
            )
            .order_by(Transaction.txn_date.desc(), Transaction.id.desc())
        )
        .scalars()
        .unique()
        .all()
    )
