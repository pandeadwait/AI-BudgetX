"""Question -> tool name + args. Returns a tool name, never a number."""

import re
from datetime import date

from sqlalchemy.orm import Session

from app.ai.llm_client import complete_json, load_prompt

TOOLS = {"spend_summary", "budget_status", "goal_feasibility", "unknown"}


def _keyword_route(question: str) -> dict:
    """Fallback when the API is down or unkeyed — the demo still answers."""
    q = question.lower()
    if any(w in q for w in ("afford", "save", "goal", "trip", "buy")):
        amount = re.search(r"(\d[\d,]*)\s*(k|thousand|lakh)?", q.replace("₹", ""))
        value = 0.0
        if amount:
            value = float(amount.group(1).replace(",", ""))
            value *= {"k": 1_000, "thousand": 1_000, "lakh": 100_000}.get(amount.group(2), 1)
        return {"tool": "goal_feasibility", "args": {"amount": value, "by": None}}
    if any(w in q for w in ("budget", "limit", "over", "left")):
        return {"tool": "budget_status", "args": {}}
    if any(w in q for w in ("spend", "spent", "where", "much", "summary")):
        return {"tool": "spend_summary", "args": {}}
    return {"tool": "unknown", "args": {}}


def route(db: Session, question: str) -> dict:
    fallback = _keyword_route(question)
    result = complete_json(
        db,
        "chat_router",
        load_prompt("chat_router", question=question, today=date.today().isoformat()),
        fallback=fallback,
    )
    # Whitelist check: an unknown tool name becomes a clarifying question, not a crash.
    if result.get("tool") not in TOOLS:
        return {"tool": "unknown", "args": {}}
    return {"tool": result["tool"], "args": result.get("args") or {}}
