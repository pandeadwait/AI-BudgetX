from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

ORM = ConfigDict(from_attributes=True)


class CategoryOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    is_essential: bool


class TransactionIn(BaseModel):
    amount: float = Field(gt=0, le=10_000_000)
    category_id: int
    note: str | None = Field(default=None, max_length=280)
    txn_date: date | None = None
    txn_type: str = "expense"

    @field_validator("txn_date")
    @classmethod
    def no_future(cls, v: date | None) -> date | None:
        if v and v > date.today():
            raise ValueError("txn_date cannot be in the future")
        return v


class TransactionOut(BaseModel):
    model_config = ORM
    id: int
    amount: float
    category_id: int
    note: str | None
    txn_date: date
    txn_type: str
    enrich_status: str


class AlertOut(BaseModel):
    model_config = ORM
    id: int
    budget_id: int
    level: str
    message: str
    spent_at_trigger: float
    limit_at_trigger: float
    projected_at_trigger: float
    is_read: bool


class TransactionResult(BaseModel):
    """One round trip: the transaction and the alert it triggered."""

    transaction: TransactionOut
    alert: AlertOut | None = None
    reallocation: str | None = None


class BudgetIn(BaseModel):
    category_id: int
    limit_amount: float = Field(gt=0, le=10_000_000)
    period_start: date | None = None


class BudgetOut(BaseModel):
    id: int
    category_id: int
    category_name: str
    limit_amount: float
    period_start: date
    period_end: date
    spent: float
    pct_used: float
    projected_total: float
    projected_over: float  # 0.0 unless materially over — never recompute this
    days_left: int


class GoalIn(BaseModel):
    name: str = Field(max_length=80)
    target_amount: float = Field(gt=0, le=100_000_000)
    target_date: date
    saved_amount: float = Field(default=0, ge=0)


class GoalOut(BaseModel):
    model_config = ORM
    id: int
    name: str
    target_amount: float
    target_date: date
    saved_amount: float


class GoalPlan(BaseModel):
    goal: GoalOut
    months_left: float
    remaining: float
    monthly_required: float
    avg_monthly_surplus: float
    gap: float
    feasible: bool
    top_flex_category: str | None
    verdict: str


class CategorySpend(BaseModel):
    category_id: int
    category_name: str
    is_essential: bool
    total: float


class SummaryOut(BaseModel):
    month: str
    total_spent: float
    total_income: float
    by_category: list[CategorySpend]
    mom_delta: float
    mom_delta_pct: float


class SimulateIn(BaseModel):
    category_id: int
    pct_change: float = Field(ge=-100, le=100)
    goal_id: int | None = None


class SimulateOut(BaseModel):
    category_name: str
    monthly_delta: float
    new_avg_surplus: float
    goal_name: str | None = None
    old_target_date: date | None = None
    old_reach_date: date | None = None
    new_reach_date: date | None = None
    months_saved: float | None = None


class ChatIn(BaseModel):
    message: str = Field(max_length=500)
    session_id: str = Field(default="demo", max_length=40)


class ChatOut(BaseModel):
    reply: str
    tool_used: str | None
    facts: dict


class AuditOut(BaseModel):
    model_config = ORM
    id: int
    task: str
    model: str
    prompt_sent: str
    fields_stripped: str
    response: str
    latency_ms: int
    cache_hit: bool
