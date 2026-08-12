"""Tool-calling entry point.

The model picks a tool and its arguments. The tool is deterministic Python.
The model then narrates the numbers the tool returned. It never produces one.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.ai import narrate, router as tool_router
from app.db import DEMO_USER_ID, get_db
from app.models import ChatMessage
from app.schemas import ChatIn, ChatOut
from app.services import budgets as budget_svc
from app.services import goals as goal_svc
from app.services import spending

router = APIRouter(tags=["chat"])


def _parse_date(value: object, default_months: int = 6) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return date.today() + timedelta(days=30 * default_months)


def run_tool(db: Session, name: str, args: dict) -> tuple[dict, str]:
    """Returns (facts, deterministic sentence). The sentence is what the user
    sees if the provider is down, so it has to stand on its own."""
    if name == "spend_summary":
        start, end = spending.month_bounds(args.get("month"))
        agg = spending.totals(db, DEMO_USER_ID, start, end)
        cats = spending.by_category(db, DEMO_USER_ID, start, end)
        delta, delta_pct = spending.mom_delta(db, DEMO_USER_ID, args.get("month"))
        facts = {
            "month": f"{start:%Y-%m}",
            "total_spent": agg["expense"],
            "top_categories": cats[:3],
            "mom_delta": delta,
            "mom_delta_pct": delta_pct,
        }
        top = cats[0]["category_name"] if cats else "nothing yet"
        return facts, (
            f"You've spent ₹{agg['expense']:,.0f} in {start:%B}, "
            f"{'up' if delta >= 0 else 'down'} ₹{abs(delta):,.0f} on last month. "
            f"Biggest category: {top}."
        )

    if name == "budget_status":
        states = [budget_svc.status(db, b) for b in budget_svc.for_month(db, DEMO_USER_ID, args.get("month"))]
        risky = [s for s in states if s["projected_over"]]
        facts = {"budgets": states, "projected_over": [s["category_name"] for s in risky]}
        if not states:
            return facts, "You haven't set any budgets for this month yet."
        if not risky:
            return facts, f"All {len(states)} budgets are on pace."
        worst = max(risky, key=lambda s: s["projected_over"])
        return facts, (
            f"{worst['category_name']} is the problem: ₹{worst['spent']:,.0f} of "
            f"₹{worst['limit_amount']:,.0f} used, projected to finish at "
            f"₹{worst['projected_total']:,.0f}."
        )

    if name == "goal_feasibility":
        amount = float(args.get("amount") or 0)
        facts = goal_svc.affordability(db, DEMO_USER_ID, amount, _parse_date(args.get("by")))
        return facts, facts["verdict"]

    return {}, (
        "I can help with what you've spent, how your budgets are pacing, or "
        "whether you can afford something by a date. Which one?"
    )


@router.post("/chat", response_model=ChatOut)
def chat(payload: ChatIn, db: Session = Depends(get_db)):
    decision = tool_router.route(db, payload.message)
    facts, deterministic = run_tool(db, decision["tool"], decision["args"])

    reply = (
        deterministic
        if decision["tool"] == "unknown"
        else narrate.answer(db, payload.message, facts, deterministic)
    )

    db.add_all(
        [
            ChatMessage(
                user_id=DEMO_USER_ID, session_id=payload.session_id,
                role="user", content=payload.message,
            ),
            ChatMessage(
                user_id=DEMO_USER_ID, session_id=payload.session_id,
                role="assistant", content=reply, tool_used=decision["tool"],
            ),
        ]
    )
    db.commit()
    return ChatOut(reply=reply, tool_used=decision["tool"], facts=facts)
