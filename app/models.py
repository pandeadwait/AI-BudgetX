from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Money


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    # Storage minimisation: hash only, never plaintext contact data.
    email_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    is_essential: Mapped[bool] = mapped_column(Boolean, default=False)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_txn_user_date", "user_id", "txn_date"),
        Index("ix_txn_user_cat_date", "user_id", "category_id", "txn_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    # Always positive. Direction lives in txn_type — signed amounts plus a type
    # column is how you get double-negation bugs at 2am.
    amount: Mapped[float] = mapped_column(Money)
    txn_type: Mapped[str] = mapped_column(String(10), default="expense")
    note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    txn_date: Mapped[date] = mapped_column(Date, default=date.today)
    # Enrichment is never allowed to block the insert.
    enrich_status: Mapped[str] = mapped_column(String(12), default="pending")
    tags: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    category: Mapped[Category] = relationship(lazy="joined")


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", "period_start", name="uq_budget_period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    limit_amount: Mapped[float] = mapped_column(Money)
    # Dates, not a "2026-03" string: same table handles weekly budgets and every
    # "spent so far" query stays a plain BETWEEN.
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    category: Mapped[Category] = relationship(lazy="joined")


class BudgetAlert(Base):
    __tablename__ = "budget_alerts"
    # This unique constraint IS the cooldown: each level fires exactly once
    # per budget period.
    __table_args__ = (UniqueConstraint("budget_id", "level", name="uq_alert_once"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    budget_id: Mapped[int] = mapped_column(ForeignKey("budgets.id"))
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    # Snapshots keep alert history truthful after the user edits the budget.
    spent_at_trigger: Mapped[float] = mapped_column(Money)
    limit_at_trigger: Mapped[float] = mapped_column(Money)
    projected_at_trigger: Mapped[float] = mapped_column(Money)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(80))
    target_amount: Mapped[float] = mapped_column(Money)
    target_date: Mapped[date] = mapped_column(Date)
    # Denormalised so the progress bar is a single-row read.
    saved_amount: Mapped[float] = mapped_column(Money, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(String(40), index=True)
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    tool_used: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class LLMAuditLog(Base):
    __tablename__ = "llm_audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    task: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(60))
    # The exact redacted payload that left the machine. The privacy proof.
    prompt_sent: Mapped[str] = mapped_column(Text)
    fields_stripped: Mapped[str] = mapped_column(String(200), default="")
    response: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
