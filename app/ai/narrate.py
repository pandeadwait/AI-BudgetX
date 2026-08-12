"""Computed facts -> prose. The LLM writes the sentence, never the numbers."""

import json

from sqlalchemy.orm import Session

from app.ai.llm_client import complete, load_prompt


def alert(db: Session, facts: dict, deterministic: str) -> str:
    """`deterministic` is both the fallback and the safety net — it already
    contains every figure, so a failed call costs wording, not correctness."""
    return complete(
        db,
        "narrate_alert",
        load_prompt("narrate_alert", facts=json.dumps(facts, default=str)),
        temperature=0.7,
        fallback=deterministic,
    )


def answer(db: Session, question: str, facts: dict, deterministic: str) -> str:
    return complete(
        db,
        "narrate_answer",
        load_prompt(
            "narrate_answer", question=question, facts=json.dumps(facts, default=str)
        ),
        temperature=0.4,
        fallback=deterministic,
    )
