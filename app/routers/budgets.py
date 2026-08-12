import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.llm_client import complete_json, load_prompt
from app.db import DEMO_USER_ID, get_db
from app.models import Budget, BudgetAlert, Category
from app.schemas import AlertOut, BudgetIn, BudgetOut
from app.services import budgets as budget_svc
from app.services import simulate
from app.services.spending import month_bounds

router = APIRouter(tags=["budgets"])


@router.post("/budgets", response_model=BudgetOut)
def create_budget(payload: BudgetIn, db: Session = Depends(get_db)):
    if not db.get(Category, payload.category_id):
        raise HTTPException(404, "unknown category")
    start, end = month_bounds(payload.period_start.strftime("%Y-%m") if payload.period_start else None)

    existing = db.execute(
        select(Budget).where(
            Budget.user_id == DEMO_USER_ID,
            Budget.category_id == payload.category_id,
            Budget.period_start == start,
        )
    ).scalar()

    if existing:
        existing.limit_amount = round(payload.limit_amount, 2)
        db.commit()
        db.refresh(existing)
        return budget_svc.status(db, existing)

    budget = Budget(
        user_id=DEMO_USER_ID,
        category_id=payload.category_id,
        limit_amount=round(payload.limit_amount, 2),
        period_start=start,
        period_end=end,
    )
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return budget_svc.status(db, budget)


@router.put("/budgets/{budget_id}", response_model=BudgetOut)
def update_budget(budget_id: int, payload: BudgetIn, db: Session = Depends(get_db)):
    budget = db.get(Budget, budget_id)
    if not budget or budget.user_id != DEMO_USER_ID:
        raise HTTPException(404, "budget not found")

    budget.limit_amount = round(payload.limit_amount, 2)
    db.commit()
    db.refresh(budget)
    return budget_svc.status(db, budget)



@router.get("/budgets", response_model=list[BudgetOut])
def list_budgets(month: str | None = None, db: Session = Depends(get_db)):
    return [budget_svc.status(db, b) for b in budget_svc.for_month(db, DEMO_USER_ID, month)]


@router.post("/budgets/suggest")
def suggest_budgets(month: str | None = None, db: Session = Depends(get_db)):
    """LLM proposes limits from the user's own history. Categories and the
    history figures are computed here; the model only chooses the number, and
    anything it returns is clamped before it is shown."""
    history = [
        {
            "category_id": c.id,
            "category": c.name,
            "essential": c.is_essential,
            "avg_monthly_spend": simulate.avg_monthly_category_spend(db, DEMO_USER_ID, c.id),
        }
        for c in db.execute(select(Category)).scalars()
    ]
    history = [h for h in history if h["avg_monthly_spend"] > 0]

    # Deterministic fallback: hold essentials at their average, trim the rest 10%.
    fallback = {
        "suggestions": [
            {
                "category_id": h["category_id"],
                "category": h["category"],
                "limit_amount": round(h["avg_monthly_spend"] * (1.0 if h["essential"] else 0.9), -1),
                "reason": "your 3-month average" + ("" if h["essential"] else ", trimmed 10%"),
            }
            for h in history
        ]
    }

    result = complete_json(
        db,
        "budget_suggest",
        load_prompt("budget_suggest", history=json.dumps(history, ensure_ascii=False)),
        temperature=0.3,
        fallback=fallback,
    )

    known = {h["category_id"]: h for h in history}
    clean = []
    for item in result.get("suggestions", []):
        entry = known.get(item.get("category_id"))
        if not entry:
            continue  # model invented a category the user never spent in
        limit = float(item.get("limit_amount") or 0)
        # Clamp: a suggestion is advice, not arithmetic we trust blindly.
        limit = min(max(limit, entry["avg_monthly_spend"] * 0.5), entry["avg_monthly_spend"] * 1.5)
        clean.append(
            {
                "category_id": entry["category_id"],
                "category": entry["category"],
                "avg_monthly_spend": entry["avg_monthly_spend"],
                "limit_amount": round(limit, -1),
                "reason": str(item.get("reason", ""))[:140],
            }
        )
    return {"suggestions": clean or fallback["suggestions"]}


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(unread: bool = False, db: Session = Depends(get_db)):
    query = (
        select(BudgetAlert)
        .join(Budget, Budget.id == BudgetAlert.budget_id)
        .where(Budget.user_id == DEMO_USER_ID)
        .order_by(BudgetAlert.created_at.desc())
    )
    if unread:
        query = query.where(BudgetAlert.is_read.is_(False))
    return db.execute(query).scalars().all()
