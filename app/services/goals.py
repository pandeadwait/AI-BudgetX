"""Goal feasibility. Deterministic — no LLM here."""

from datetime import date

from sqlalchemy.orm import Session

from app.models import Goal
from app.services import spending
from app.services.spending import DAYS_PER_MONTH


def months_between(start: date, end: date) -> float:
    return max((end - start).days / DAYS_PER_MONTH, 0.0)


def plan(db: Session, goal: Goal, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    months_left = round(months_between(as_of, goal.target_date), 2)
    remaining = round(max(goal.target_amount - goal.saved_amount, 0.0), 2)

    if remaining == 0:
        monthly_required = 0.0
    elif months_left == 0:
        # Date has passed (or is today): the whole remainder is due now.
        monthly_required = remaining
    else:
        monthly_required = round(remaining / months_left, 2)

    surplus = spending.avg_monthly_surplus(db, goal.user_id)
    gap = round(max(monthly_required - surplus, 0.0), 2)
    feasible = remaining == 0 or (months_left > 0 and surplus >= monthly_required)
    flex = spending.top_flex_category(db, goal.user_id)

    if remaining == 0:
        verdict = f"'{goal.name}' is already funded."
    elif months_left == 0:
        verdict = f"The target date for '{goal.name}' has passed with ₹{remaining:,.0f} still to go."
    elif feasible:
        verdict = (
            f"On track: ₹{monthly_required:,.0f}/month needed, "
            f"you're averaging ₹{surplus:,.0f}."
        )
    else:
        verdict = (
            f"You'd need ₹{monthly_required:,.0f}/month but you're averaging "
            f"₹{surplus:,.0f} — a ₹{gap:,.0f} gap."
        )
        if flex:
            verdict += f" {flex['category_name']} is your biggest flexible spend."

    return {
        "months_left": months_left,
        "remaining": remaining,
        "monthly_required": monthly_required,
        "avg_monthly_surplus": surplus,
        "gap": gap,
        "feasible": feasible,
        "top_flex_category": flex["category_name"] if flex else None,
        "verdict": verdict,
    }


def affordability(db: Session, user_id: int, amount: float, by: date) -> dict:
    """Same math as plan(), for a hypothetical goal the user hasn't saved yet."""
    ghost = Goal(
        user_id=user_id, name=f"₹{amount:,.0f} target", target_amount=amount,
        target_date=by, saved_amount=0.0,
    )
    return plan(db, ghost) | {"amount": amount, "by": by.isoformat()}
