# AI BudgetX

An AI financial assistant that intervenes *at the moment of entry*, not after the
money is gone. See [Plan.md](Plan.md) for the full brief.

**The one architectural rule: the LLM never touches money math and never sees raw PII.**
Deterministic Python computes every number. The model classifies, routes and narrates.

## Run it

### Database

Supabase is Postgres, so it is a connection string and nothing else. In the
Supabase dashboard: **Project Settings → Database → Connection string → URI**.
Take the **Session pooler** (port 5432), put it in `.env` as `DATABASE_URL`,
and paste it exactly as given — the `postgresql://` prefix is rewritten to
psycopg and `sslmode=require` is appended automatically. URL-encode the password
if it contains `@ : / ? # &`.

One hosted database means all five of us share the same data: P3's UI sees P1's
transactions without waiting for anyone to re-seed.

Tables are created on first boot, in the `public` schema. Nothing else about the
app changes — no Supabase SDK, no client library, same SQLAlchemy models.

**One thing to know before anyone shares a key:** our API connects as the
database owner and does its own `user_id` filtering, so row-level security is
not in play. Supabase also auto-generates a REST API over these same tables. If
anyone exposes the anon key, enable RLS on every table first — without it that
key reads the whole database.

<details>
<summary>Local Postgres instead</summary>

```bash
brew install postgresql@17
pg_ctl -D /opt/homebrew/var/postgresql@17 -l /tmp/pg17.log start
createdb budgetx
# DATABASE_URL=postgresql+psycopg://localhost/budgetx
```

`brew services` is broken on this machine (an unrelated Homebrew bug), so start
the server with `pg_ctl` directly; `pg_ctl -D ... stop` to shut it down.
</details>

### App

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # optional — works without an LLM API key

.venv/bin/uvicorn app.main:app --reload      # API + docs on :8000/docs
.venv/bin/streamlit run ui/app.py            # UI on :8501
```

The database seeds itself on first boot: one user, 10 categories, 3 finished
months plus the current month to date, 6 budgets and a goal. Reseed from scratch
with `.venv/bin/python -m app.seed`.

```bash
.venv/bin/python -m pytest        # deterministic layer
```

The tests run on in-memory SQLite so they need no server and finish in under a
second. Nothing in the models or queries is dialect-specific — that portability
is the point of the ORM layer, and the test suite proves it every run.

## What runs without an API key

Every LLM call degrades to a deterministic fallback: keyword tool routing, the
pre-computed alert sentence, keyword theme clustering, average-based budget
suggestions. The degradation is recorded in `/audit/llm-calls` rather than hidden.
Responses are cached to disk by `sha256(prompt)`, so a rate limit mid-demo
replays instead of failing.

## Layout

```
app/
  db.py models.py schemas.py main.py seed.py
  services/     spending, budgets, alerts, goals, simulate — no LLM imports
  ai/           llm_client (the only provider chokepoint), redact, narrate, router
  ai/prompts/   versioned .txt files
  routers/      transactions, budgets, goals, insights, chat, audit
ui/app.py       Streamlit
tests/          the math layer
```

## Endpoints

| | |
|---|---|
| `POST /transactions` | insert + alert + reallocation in one round trip |
| `GET /transactions?month=` · `GET /categories` | |
| `POST /budgets` · `GET /budgets?month=` | each with spent, pct, projection, days left |
| `POST /budgets/suggest` | limits proposed from actual history, clamped server-side |
| `POST /goals` · `GET /goals` · `GET /goals/{id}/plan` | feasibility, monthly required, gap |
| `GET /insights/summary?month=` | totals, by category, month-over-month |
| `GET /insights/themes` | behavioural clustering over notes only |
| `POST /simulate` | cut a category, watch the goal date move |
| `GET /alerts?unread=true` | |
| `POST /chat` | tool-calling entry point |
| `GET /audit/llm-calls` | every payload that left the machine |

## Not built yet

- Note enrichment (`ai/enrich.py`) and `MerchantHint` category autofill
- Goal contribution ledger (the denormalised `saved_amount` total is there)
- `docs/BRD.md`, `docs/HLD.md`
