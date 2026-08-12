import json
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm_client import complete_json, load_prompt
from app.db import DEMO_USER_ID, get_db
from app.models import Goal, Transaction
from app.schemas import SimulateIn, SimulateOut, SummaryOut
from app.services import simulate as sim_svc
from app.services import spending
from app.services.spending import month_bounds

router = APIRouter(tags=["insights"])

# Fallback clustering when the API is unavailable. Crude on purpose — it exists
# so the demo never shows an empty panel, not to compete with the model.
THEME_WORDS = {
    "tired or rushed": ("tired", "late", "lazy", "no time", "busy", "rushed"),
    "social": ("friends", "party", "team", "birthday", "treat", "date"),
    "convenience": ("delivery", "ordered in", "cab", "quick", "auto"),
    "stress or reward": ("stress", "deserve", "bad day", "cheer", "bored"),
}


@router.get("/insights/summary", response_model=SummaryOut)
def summary(month: str | None = None, db: Session = Depends(get_db)):
    start, end = month_bounds(month)
    agg = spending.totals(db, DEMO_USER_ID, start, end)
    delta, delta_pct = spending.mom_delta(db, DEMO_USER_ID, month)
    return SummaryOut(
        month=f"{start:%Y-%m}",
        total_spent=agg["expense"],
        total_income=agg["income"],
        by_category=spending.by_category(db, DEMO_USER_ID, start, end),
        mom_delta=delta,
        mom_delta_pct=delta_pct,
    )


@router.get("/insights/themes")
def themes(months: int = 3, db: Session = Depends(get_db)):
    """Behavioural clustering over notes only — no amounts, no dates, no ids
    leave the machine (redaction in llm_client strips PII on top of that)."""
    start = spending.shift_month(month_bounds()[0], -(months - 1))
    rows = db.execute(
        select(Transaction.note, Transaction.category_id)
        .where(
            Transaction.user_id == DEMO_USER_ID,
            Transaction.note.is_not(None),
            Transaction.txn_date >= start,
        )
        .limit(120)
    ).all()
    notes = [{"note": note, "category_id": cid} for note, cid in rows if note]
    if not notes:
        return {"themes": [], "note_count": 0}

    counts = Counter()
    for item in notes:
        text = item["note"].lower()
        for theme, words in THEME_WORDS.items():
            if any(word in text for word in words):
                counts[theme] += 1
    fallback = {
        "themes": [
            {"theme": theme, "count": count, "insight": f"{count} notes mention this."}
            for theme, count in counts.most_common(4)
        ]
    }

    result = complete_json(
        db,
        "themes",
        load_prompt("themes", notes=json.dumps([n["note"] for n in notes])),
        temperature=0.4,
        fallback=fallback,
    )
    return {"themes": result.get("themes", [])[:4], "note_count": len(notes)}


@router.post("/simulate", response_model=SimulateOut)
def what_if(payload: SimulateIn, db: Session = Depends(get_db)):
    goal = db.get(Goal, payload.goal_id) if payload.goal_id else None
    return SimulateOut(
        **sim_svc.run(db, DEMO_USER_ID, payload.category_id, payload.pct_change, goal)
    )
