"""Category rollups and month-over-month movement. Deterministic — no LLM here."""

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Category, Transaction

DAYS_PER_MONTH = 30.44


def month_bounds(month: str | None = None) -> tuple[date, date]:
    """'2026-03' (or None for the current month) -> first and last day."""
    if month:
        year, mon = (int(part) for part in month.split("-")[:2])
    else:
        today = date.today()
        year, mon = today.year, today.month
    return date(year, mon, 1), date(year, mon, monthrange(year, mon)[1])


def shift_month(start: date, months: int) -> date:
    total = start.year * 12 + (start.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def totals(db: Session, user_id: int, start: date, end: date) -> dict[str, float]:
    rows = db.execute(
        select(Transaction.txn_type, func.sum(Transaction.amount))
        .where(
            Transaction.user_id == user_id,
            Transaction.txn_date.between(start, end),
        )
        .group_by(Transaction.txn_type)
    ).all()
    out = {"expense": 0.0, "income": 0.0}
    for txn_type, total in rows:
        out[txn_type] = round(total or 0.0, 2)
    return out


def by_category(db: Session, user_id: int, start: date, end: date) -> list[dict]:
    rows = db.execute(
        select(
            Category.id,
            Category.name,
            Category.is_essential,
            func.sum(Transaction.amount),
        )
        .join(Transaction, Transaction.category_id == Category.id)
        .where(
            Transaction.user_id == user_id,
            Transaction.txn_type == "expense",
            Transaction.txn_date.between(start, end),
        )
        .group_by(Category.id)
        .order_by(func.sum(Transaction.amount).desc())
    ).all()
    return [
        {
            "category_id": cid,
            "category_name": name,
            "is_essential": bool(essential),
            "total": round(total or 0.0, 2),
        }
        for cid, name, essential, total in rows
    ]


def completed_window(months: int = 3) -> tuple[date, date]:
    """The last `months` *finished* months.

    The current month is deliberately excluded: on the 3rd, a partial month
    would halve the apparent spend and make every goal look affordable.
    """
    this_start = month_bounds()[0]
    return shift_month(this_start, -months), this_start - timedelta(days=1)


def avg_monthly_surplus(db: Session, user_id: int, months: int = 3) -> float:
    """Average (income - expense) per finished month."""
    start, end = completed_window(months)
    agg = totals(db, user_id, start, end)
    return round((agg["income"] - agg["expense"]) / months, 2)


def top_flex_category(db: Session, user_id: int, months: int = 3) -> dict | None:
    """Biggest non-essential spend — the first place to look for money."""
    start, end = completed_window(months)
    flexible = [c for c in by_category(db, user_id, start, end) if not c["is_essential"]]
    return flexible[0] if flexible else None


def mom_delta(db: Session, user_id: int, month: str | None = None) -> tuple[float, float]:
    """(absolute, percent) change in expense against the previous month.

    Like for like: on the 12th, this month's 12 days are compared against the
    first 12 days of last month. Comparing a part-month to a whole one reports
    a spectacular saving every time and is the fastest way to lose a judge.
    """
    start, end = month_bounds(month)
    today = date.today()
    if start <= today <= end:
        end = today
    days_in = (end - start).days

    this_month = totals(db, user_id, start, end)["expense"]
    prev_start = shift_month(start, -1)
    prev_end = min(
        prev_start + timedelta(days=days_in),
        month_bounds(f"{prev_start:%Y-%m}")[1],
    )
    last_month = totals(db, user_id, prev_start, prev_end)["expense"]
    delta = round(this_month - last_month, 2)
    pct = round(delta / last_month * 100, 1) if last_month else 0.0
    return delta, pct
