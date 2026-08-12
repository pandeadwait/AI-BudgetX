from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import narrate
from app.db import DEMO_USER_ID, get_db
from app.models import Budget, Category, Transaction
from app.schemas import CategoryIn, CategoryOut, TransactionIn, TransactionOut, TransactionResult
from app.services import alerts as alert_svc
from app.services import budgets as budget_svc
from app.services.spending import month_bounds

router = APIRouter(tags=["transactions"])


@router.get("/categories", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return db.execute(select(Category).order_by(Category.name)).scalars().all()


@router.post("/categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def add_category(payload: CategoryIn, db: Session = Depends(get_db)):
    cleaned_name = payload.name.strip()
    existing = db.execute(
        select(Category).where(func.lower(Category.name) == cleaned_name.lower())
    ).scalar()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category with this name already exists",
        )

    category = Category(name=cleaned_name, is_essential=payload.is_essential)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, db: Session = Depends(get_db)):
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")

    total_categories = db.execute(select(func.count(Category.id))).scalar() or 0
    if total_categories <= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete category: minimum 5 categories must be maintained",
        )

    txn_count = (
        db.execute(
            select(func.count(Transaction.id)).where(Transaction.category_id == category_id)
        ).scalar()
        or 0
    )
    if txn_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete category: transactions exist for this category",
        )

    budget_count = (
        db.execute(
            select(func.count(Budget.id)).where(Budget.category_id == category_id)
        ).scalar()
        or 0
    )
    if budget_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete category: active budgets exist for this category",
        )

    db.delete(category)
    db.commit()
    return {"status": "deleted", "id": category_id}



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
        # Every figure the sentence could possibly need is precomputed and
        # named for exactly what it is. Anything missing here is something the
        # model would otherwise be tempted to work out for itself.
        facts = {
            "level": alert.level,
            "category": txn.category.name,
            "already_spent": f"₹{alert.spent_at_trigger:,.0f}",
            "budget_limit": f"₹{alert.limit_at_trigger:,.0f}",
            "still_left_to_spend": f"₹{max(alert.limit_at_trigger - alert.spent_at_trigger, 0):,.0f}",
            "projected_total_by_period_end": f"₹{alert.projected_at_trigger:,.0f}",
            "projected_amount_over_limit": f"₹{state['projected_over']:,.0f}",
            "pct_of_budget_used": f"{state['pct_used']:.0f}%",
            "days_left_in_period": state["days_left"],
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
