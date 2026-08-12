"""The deterministic layer, tested hard. LLM wording is not tested — it is
unstable by design and asserting on it just breaks the build.

ponytail: one file, not three. Split per-service when it stops fitting on a
screen and a rerun costs more than a glance.
"""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base, engine_kwargs, normalise
from app.models import Budget, Category, Goal, Transaction, User
from app.schemas import TransactionIn
from app.services import alerts, budgets, goals
from app.services.alerts import BREACH, INFO, PROJECTED_OVERRUN, WARN


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                User(id=1, name="t", email_hash="x"),
                Category(id=1, name="Food", is_essential=False),
                Category(id=2, name="Rent", is_essential=True),
            ]
        )
        session.commit()
        yield session


def make_budget(db, limit=10_000, start=None, days=30, category_id=1):
    start = start or date.today().replace(day=1)
    budget = Budget(
        user_id=1,
        category_id=category_id,
        limit_amount=limit,
        period_start=start,
        period_end=start + timedelta(days=days - 1),
    )
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return budget


def spend(db, amount, on, category_id=1, txn_type="expense"):
    txn = Transaction(
        user_id=1, category_id=category_id, amount=amount, txn_date=on, txn_type=txn_type
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# --- thresholds -----------------------------------------------------------

@pytest.mark.parametrize(
    "pct, projected, expected",
    [
        (49.9, 0, None),
        (50.0, 0, INFO),          # exact boundary fires
        (79.9, 0, INFO),
        (80.0, 0, WARN),          # exact boundary fires
        (99.9, 0, WARN),
        (100.0, 0, BREACH),       # exact boundary fires
        (120.0, 999_999, BREACH), # breach outranks projection
        (40.0, 10_001, PROJECTED_OVERRUN),  # pace beats the 80% line
        (85.0, 10_001, PROJECTED_OVERRUN),
        (40.0, 10_000, None),     # projection equal to limit is not an overrun
    ],
)
def test_pick_level(pct, projected, expected):
    assert alerts.pick_level(pct, projected, limit=10_000) == expected


def test_projection_on_day_one_does_not_divide_by_zero(db):
    start = date.today()
    budget = make_budget(db, limit=10_000, start=start, days=30)
    spend(db, 500, on=start)
    state = budgets.status(db, budget, as_of=start)
    # One day elapsed of 30: 500/1*30.
    assert state["projected_total"] == 15_000
    assert state["days_left"] == 29


def test_backdated_transaction_does_not_project_past_period_end(db):
    start = date.today().replace(day=1)
    budget = make_budget(db, limit=10_000, start=start, days=30)
    spend(db, 3_000, on=start)
    state = budgets.status(db, budget, as_of=start - timedelta(days=10))
    assert state["projected_total"] <= 90_000
    assert state["days_left"] >= 0


# --- cooldown -------------------------------------------------------------

def test_same_level_fires_exactly_once(db):
    start = date.today().replace(day=1)
    budget = make_budget(db, limit=10_000, start=start, days=30)

    first = alerts.evaluate(db, spend(db, 9_000, on=start + timedelta(days=25)))
    assert first is not None
    second = alerts.evaluate(db, spend(db, 100, on=start + timedelta(days=26)))
    assert second is None, "cooldown broken: same level fired twice"


def test_escalating_level_still_fires(db):
    start = date.today().replace(day=1)
    budget = make_budget(db, limit=10_000, start=start, days=30)

    warn = alerts.evaluate(db, spend(db, 8_500, on=start + timedelta(days=27)))
    assert warn.level == WARN
    breach = alerts.evaluate(db, spend(db, 2_000, on=start + timedelta(days=28)))
    assert breach.level == BREACH


# --- empty states ---------------------------------------------------------

def test_budget_with_no_transactions(db):
    budget = make_budget(db)
    state = budgets.status(db, budget)
    assert (state["spent"], state["pct_used"], state["projected_total"]) == (0.0, 0.0, 0.0)


def test_transaction_with_no_budget_is_silent(db):
    assert alerts.evaluate(db, spend(db, 5_000, on=date.today(), category_id=2)) is None


def test_income_never_triggers_an_alert(db):
    make_budget(db)
    txn = spend(db, 50_000, on=date.today(), txn_type="income")
    assert alerts.evaluate(db, txn) is None


# --- goals ----------------------------------------------------------------

def test_goal_already_achieved_is_feasible(db):
    goal = Goal(
        user_id=1, name="done", target_amount=1_000,
        target_date=date.today() + timedelta(days=90), saved_amount=1_000,
    )
    plan = goals.plan(db, goal)
    assert plan["remaining"] == 0 and plan["feasible"] and plan["monthly_required"] == 0


def test_goal_target_date_in_the_past(db):
    goal = Goal(
        user_id=1, name="late", target_amount=10_000,
        target_date=date.today() - timedelta(days=10), saved_amount=0,
    )
    plan = goals.plan(db, goal)
    assert plan["months_left"] == 0
    assert plan["monthly_required"] == 10_000  # the whole remainder is due now
    assert not plan["feasible"]


def test_goal_with_zero_surplus_is_not_feasible(db):
    goal = Goal(
        user_id=1, name="trip", target_amount=40_000,
        target_date=date.today() + timedelta(days=180), saved_amount=0,
    )
    plan = goals.plan(db, goal)
    assert plan["avg_monthly_surplus"] == 0.0
    assert plan["gap"] == plan["monthly_required"]
    assert not plan["feasible"]


# --- input validation -----------------------------------------------------

@pytest.mark.parametrize("amount", [0, -1, 10_000_001])
def test_rejected_amounts(amount):
    with pytest.raises(ValidationError):
        TransactionIn(amount=amount, category_id=1)


def test_future_dated_transaction_rejected():
    with pytest.raises(ValidationError):
        TransactionIn(amount=100, category_id=1, txn_date=date.today() + timedelta(days=1))


# --- connection handling --------------------------------------------------

SUPABASE_POOLER = "postgresql://postgres.abc:pw@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"


def test_pasted_supabase_uri_is_driven_by_psycopg_over_tls():
    url = normalise(SUPABASE_POOLER)
    assert url.startswith("postgresql+psycopg://")
    assert "sslmode=require" in url


def test_local_socket_is_not_forced_onto_tls():
    assert "sslmode" not in normalise("postgresql+psycopg://localhost/budgetx")


def test_transaction_pooler_disables_prepared_statements():
    # PgBouncer on :6543 cannot hold them; the failure surfaces minutes in.
    kwargs = engine_kwargs(normalise(SUPABASE_POOLER))
    assert kwargs["connect_args"]["prepare_threshold"] is None
    assert kwargs["poolclass"].__name__ == "NullPool"


def test_session_pooler_keeps_prepared_statements():
    session_pooler = SUPABASE_POOLER.replace(":6543", ":5432")
    assert "prepare_threshold" not in engine_kwargs(normalise(session_pooler))["connect_args"]


def test_explicit_sslmode_is_not_doubled():
    assert normalise(SUPABASE_POOLER + "?sslmode=verify-full").count("sslmode") == 1
