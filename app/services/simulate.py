"""What-if recomputation: change one category's spend, see the goal date move.

Deterministic — no LLM here.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Goal, Transaction
from app.services import spending
from app.services.spending import DAYS_PER_MONTH


def avg_monthly_category_spend(
    db: Session, user_id: int, category_id: int, months: int = 3
) -> float:
    start, end = spending.completed_window(months)
    total = db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.user_id == user_id,
            Transaction.category_id == category_id,
            Transaction.txn_type == "expense",
            Transaction.txn_date.between(start, end),
        )
    ).scalar()
    return round((total or 0.0) / months, 2)


def run(
    db: Session, user_id: int, category_id: int, pct_change: float, goal: Goal | None
) -> dict:
    category = db.get(Category, category_id)
    avg = avg_monthly_category_spend(db, user_id, category_id)
    # Negative pct_change = spending less = surplus goes up.
    monthly_delta = round(avg * pct_change / 100, 2)
    surplus = spending.avg_monthly_surplus(db, user_id)
    new_surplus = round(surplus - monthly_delta, 2)

    out = {
        "category_name": category.name if category else "unknown",
        "monthly_delta": monthly_delta,
        "new_avg_surplus": new_surplus,
    }
    if not goal:
        return out

    remaining = max(goal.target_amount - goal.saved_amount, 0.0)
    old_months = remaining / surplus if surplus > 0 else None
    new_months = remaining / new_surplus if new_surplus > 0 else None

    def reach(months: float | None) -> date | None:
        return date.today() + timedelta(days=months * DAYS_PER_MONTH) if months else None

    return out | {
        "goal_name": goal.name,
        "old_target_date": goal.target_date,
        # Both reach dates are "at this surplus", so they are comparable with
        # each other — the goal's own target date is a third, different thing.
        "old_reach_date": reach(old_months),
        "new_reach_date": reach(new_months),
        "months_saved": (
            round(old_months - new_months, 1) if old_months and new_months else None
        ),
    }
