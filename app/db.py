import os

from sqlalchemy import Numeric, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

# Supabase is Postgres, so switching to it is a connection string, not a rewrite.
# Paste the URI from Supabase → Project Settings → Database → Connection string.
DEFAULT_URL = "postgresql+psycopg://localhost/budgetx"

# Assumption 1 from the plan: single seeded user, no auth. Every router reads
# this instead of a session — one place to swap when auth lands.
DEMO_USER_ID = 1


def normalise(url: str) -> str:
    """Make a pasted Supabase URI work as-is."""
    # The dashboard hands out `postgresql://`, which SQLAlchemy resolves to
    # psycopg2. We install psycopg 3.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    # Financial data over the public internet is TLS or nothing. Local sockets
    # are exempt because there is no wire to sniff.
    remote = not url.startswith("sqlite") and not any(
        host in url for host in ("@localhost", "@127.0.0.1", "://localhost", "://127.0.0.1")
    )
    if remote and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


def engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}

    kwargs: dict = {"pool_pre_ping": True, "connect_args": {}}
    # Supabase's transaction pooler is PgBouncer on :6543. It multiplexes one
    # server connection across clients, so two things break: psycopg's automatic
    # prepared statements ("prepared statement _pg3_0 already exists" a few
    # minutes into the demo), and a client-side pool holding server state.
    # Session pooler (:5432) has neither problem.
    if ":6543" in url:
        kwargs["connect_args"]["prepare_threshold"] = None
        kwargs["poolclass"] = NullPool
    return kwargs


DB_URL = normalise(os.getenv("DATABASE_URL", DEFAULT_URL))
engine = create_engine(DB_URL, **engine_kwargs(DB_URL))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# NUMERIC(12,2) is a real decimal column in Postgres, so storage is exact.
# asdecimal=False keeps the Python side on float, which is what every service
# and Pydantic schema expects — and what lets the test suite run on in-memory
# SQLite, which has no native Decimal at all.
# ponytail: switch to asdecimal=True (and Decimal end to end) when money math
# grows beyond rounding rupees to 2dp — compound interest, FX, tax.
Money = Numeric(12, 2, asdecimal=False)


class Base(DeclarativeBase):
    pass


def get_db():
    with SessionLocal() as session:
        yield session
