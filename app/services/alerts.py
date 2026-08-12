"""Threshold evaluation, pace projection and cooldown. Deterministic — no LLM here.

The LLM may later re-word `message`; it never decides the level and never
computes a number.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Budget, BudgetAlert, Transaction
from app.services import budgets as budget_svc

BREACH, PROJECTED_OVERRUN, WARN, INFO = "BREACH", "PROJECTED_OVERRUN", "WARN", "INFO"


def pick_level(pct_used: float, projected: float, limit: float) -> str | None:
    # Order matters: a projected overrun is checked before the 80% line, so
    # someone burning fast on day 6 hears about it at 40% instead of waiting.
    if pct_used >= 100:
        return BREACH
    if projected > limit:
        return PROJECTED_OVERRUN
    if pct_used >= 80:
        return WARN
    if pct_used >= 50:
        return INFO
    return None


def compose(state: dict, level: str) -> str:
    name, days_left = state["category_name"], state["days_left"]
    if level == BREACH:
        over = round(state["spent"] - state["limit_amount"], 2)
        return f"You've gone ₹{over:,.0f} over your ₹{state['limit_amount']:,.0f} {name} budget."
    if level == PROJECTED_OVERRUN:
        return (
            f"You're at {state['pct_used']:.0f}% of {name} with {days_left} days left — "
            f"at this pace you'll finish at ₹{state['projected_total']:,.0f} "
            f"against a ₹{state['limit_amount']:,.0f} budget."
        )
    return (
        f"You're at {state['pct_used']:.0f}% of your ₹{state['limit_amount']:,.0f} "
        f"{name} budget with {days_left} days left."
    )


def evaluate(db: Session, txn: Transaction) -> BudgetAlert | None:
    """Returns a persisted alert, or None if nothing crossed / already fired."""
    if txn.txn_type != "expense":
        return None
    budget = budget_svc.find_active(db, txn.user_id, txn.category_id, txn.txn_date)
    if not budget:
        return None

    state = budget_svc.status(db, budget, as_of=txn.txn_date)
    level = pick_level(state["pct_used"], state["projected_total"], budget.limit_amount)
    if not level:
        return None

    # The cooldown. UniqueConstraint(budget_id, level) is the backstop; this
    # read is what keeps the happy path from raising.
    already = db.execute(
        select(BudgetAlert).where(
            BudgetAlert.budget_id == budget.id, BudgetAlert.level == level
        )
    ).scalar()
    if already:
        return None

    alert = BudgetAlert(
        budget_id=budget.id,
        transaction_id=txn.id,
        level=level,
        message=compose(state, level),
        spent_at_trigger=state["spent"],
        limit_at_trigger=budget.limit_amount,
        projected_at_trigger=state["projected_total"],
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


def reallocation(db: Session, user_id: int, over: Budget, on: date | None = None) -> str | None:
    """Find a budget tracking under that could cover another's projected overrun."""
    on = on or date.today()
    over_state = budget_svc.status(db, over, as_of=on)
    shortfall = round(over_state["projected_total"] - over.limit_amount, 2)
    if shortfall <= 0:
        return None

    slack = []
    for other in budget_svc.for_month(db, user_id, f"{over.period_start:%Y-%m}"):
        if other.id == over.id:
            continue
        state = budget_svc.status(db, other, as_of=on)
        spare = round(other.limit_amount - state["projected_total"], 2)
        if spare > 0:
            slack.append((spare, state["category_name"]))
    if not slack:
        return None

    spare, name = max(slack)
    return (
        f"{over_state['category_name']} is projected ₹{shortfall:,.0f} over, "
        f"but {name} is tracking ₹{spare:,.0f} under — shift it?"
    )
