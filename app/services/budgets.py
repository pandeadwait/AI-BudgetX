"""Spend-vs-limit and pace projection. Deterministic — no LLM here."""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Budget, Transaction
from app.services.spending import month_bounds


def spent_for(db: Session, budget: Budget, as_of: date | None = None) -> float:
    end = min(as_of, budget.period_end) if as_of else budget.period_end
    total = db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.user_id == budget.user_id,
            Transaction.category_id == budget.category_id,
            Transaction.txn_type == "expense",
            Transaction.txn_date.between(budget.period_start, end),
        )
    ).scalar()
    return round(total or 0.0, 2)


def status(db: Session, budget: Budget, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    spent = spent_for(db, budget, as_of)

    days_total = (budget.period_end - budget.period_start).days + 1
    # Clamp to [1, days_total]: day one of a period must not divide by zero, and
    # a back-dated transaction must not project off the end of the month.
    days_elapsed = min(max((as_of - budget.period_start).days + 1, 1), days_total)

    pct_used = round(spent / budget.limit_amount * 100, 1) if budget.limit_amount else 0.0
    projected = round(spent / days_elapsed * days_total, 2)

    return {
        "id": budget.id,
        "category_id": budget.category_id,
        "category_name": budget.category.name,
        "limit_amount": budget.limit_amount,
        "period_start": budget.period_start,
        "period_end": budget.period_end,
        "spent": spent,
        "pct_used": pct_used,
        "projected_total": projected,
        "days_left": max(days_total - days_elapsed, 0),
    }


def for_month(db: Session, user_id: int, month: str | None = None) -> list[Budget]:
    start, _ = month_bounds(month)
    return list(
        db.execute(
            select(Budget).where(Budget.user_id == user_id, Budget.period_start == start)
        )
        .scalars()
        .unique()
    )


def find_active(db: Session, user_id: int, category_id: int, on: date) -> Budget | None:
    return db.execute(
        select(Budget).where(
            Budget.user_id == user_id,
            Budget.category_id == category_id,
            Budget.period_start <= on,
            Budget.period_end >= on,
        )
    ).scalar()
