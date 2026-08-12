"""Deterministic demo data. The dashboard is never empty on stage.

Shapes the current month so the *pace* alert is the one that fires: dining sits
around 45% on a day when only 39% of the month has passed, and transport runs
under — which is exactly the reallocation the demo asks for.
"""

import hashlib
import random
from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import DEMO_USER_ID, Base, SessionLocal, engine
from app.models import Budget, Category, Goal, Transaction, User
from app.services.spending import month_bounds, shift_month

SALARY = 62_000

# name -> (essential, full-month spend, number of transactions, pace factor)
# pace factor 1.0 == exactly on track for today's date.
PLAN: dict[str, tuple[bool, int, int, float]] = {
    "Rent & bills": (True, 18_000, 1, 1.0),
    "Groceries": (True, 8_000, 6, 1.0),
    "Food & dining": (False, 12_000, 12, 1.18),
    "Transport": (True, 4_000, 8, 0.72),
    "Shopping": (False, 6_000, 3, 1.05),
    "Entertainment": (False, 3_000, 4, 0.9),
    "Health": (True, 2_000, 1, 0.8),
    "Education": (True, 2_500, 1, 1.0),
    "Subscriptions": (False, 1_200, 3, 1.0),
    "Other": (False, 1_500, 2, 1.0),
}

BUDGETED = ["Food & dining", "Groceries", "Transport", "Shopping", "Entertainment", "Subscriptions"]

NOTES = {
    "Food & dining": [
        "ordered in again, too tired to cook",
        "late meeting, grabbed dinner out",
        "team lunch",
        "swiggy, no energy after gym",
        "coffee run before standup",
        "friends dropped by, ordered pizza",
        "too lazy to cook, biryani",
        "birthday treat for the team",
        "late night snack, was working",
        "brunch with friends",
        "office was hectic, ordered in",
        "split dinner with rohan 9876543210",
    ],
    "Transport": ["auto to office", "cab, running late", "metro recharge", "fuel"],
    "Shopping": ["needed running shoes", "bad day, bought a jacket", "gift"],
    "Entertainment": ["movie night", "concert tickets", "bored on sunday", "bowling with friends"],
    "Groceries": ["weekly big basket", "milk and eggs", "monthly stock up"],
    "Subscriptions": ["netflix", "spotify", "icloud storage"],
    "Rent & bills": ["rent + electricity"],
    "Health": ["pharmacy"],
    "Education": ["course instalment"],
    "Other": ["misc", "donation"],
}


def _split(total: float, parts: int, rng: random.Random) -> list[float]:
    """Break an amount into `parts` uneven but plausible chunks."""
    weights = [rng.uniform(0.6, 1.6) for _ in range(parts)]
    scale = total / sum(weights)
    chunks = [round(w * scale, -1) for w in weights]
    chunks[-1] = round(total - sum(chunks[:-1]), 2)
    return chunks


def seed(db: Session, today: date | None = None) -> None:
    today = today or date.today()
    rng = random.Random(7)  # fixed: the demo looks the same at every rehearsal

    user = User(
        id=DEMO_USER_ID,
        name="Demo User",
        email_hash=hashlib.sha256(b"demo@example.com").hexdigest(),
    )
    db.add(user)
    categories = {
        name: Category(name=name, is_essential=essential)
        for name, (essential, *_rest) in PLAN.items()
    }
    db.add_all(categories.values())
    db.flush()

    this_start = month_bounds(f"{today:%Y-%m}")[0]
    days_in_month = monthrange(today.year, today.month)[1]
    elapsed_fraction = ((today - this_start).days + 1) / days_in_month

    for offset in (-3, -2, -1, 0):
        start = shift_month(this_start, offset)
        last_day = monthrange(start.year, start.month)[1]
        is_current = offset == 0
        max_day = today.day if is_current else last_day

        db.add(
            Transaction(
                user_id=user.id,
                category_id=categories["Other"].id,
                amount=SALARY,
                txn_type="income",
                note="salary",
                txn_date=start,
            )
        )

        for name, (_essential, monthly, count, pace) in PLAN.items():
            # Past months land on the full figure; the current month lands where
            # today's pace puts it.
            target = monthly * (elapsed_fraction * pace if is_current else rng.uniform(0.92, 1.08))
            parts = max(1, round(count * (elapsed_fraction if is_current else 1)))
            if target < 1:
                continue
            for amount in _split(target, parts, rng):
                if amount <= 0:
                    continue
                day = 1 if name == "Rent & bills" else rng.randint(1, max_day)
                db.add(
                    Transaction(
                        user_id=user.id,
                        category_id=categories[name].id,
                        amount=amount,
                        note=rng.choice(NOTES[name]),
                        txn_date=start + timedelta(days=day - 1),
                        enrich_status="done",
                    )
                )

    for name in BUDGETED:
        db.add(
            Budget(
                user_id=user.id,
                category_id=categories[name].id,
                limit_amount=PLAN[name][1],
                period_start=this_start,
                period_end=this_start + timedelta(days=days_in_month - 1),
            )
        )

    db.add(
        Goal(
            user_id=user.id,
            name="Emergency fund",
            target_amount=60_000,
            target_date=shift_month(this_start, 6),
            saved_amount=12_000,
        )
    )
    db.commit()


def seed_if_empty() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.execute(select(User.id)).first():
            return
        seed(db)


if __name__ == "__main__":
    Base.metadata.drop_all(engine)
    seed_if_empty()
    print("seeded budgetx.db")
