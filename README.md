# AI BudgetX

An AI financial assistant that intervenes *at the moment of entry*, not after the
money is gone. [Plan.md](Plan.md) is the full brief; [FRONTEND.md](FRONTEND.md)
is the API reference with real payloads.

**The one architectural rule: the LLM never touches money math and never sees raw PII.**
Deterministic Python computes every number. The model classifies, routes and narrates.

## Run it

### Database

Supabase is Postgres, so it is a connection string and nothing else. In the
Supabase dashboard: **Project Settings → Database → Connection string → URI**.
Put it in `.env` as `DATABASE_URL` and paste it exactly as given — the
`postgresql://` prefix is rewritten to psycopg and `sslmode=require` is appended
automatically. URL-encode the password if it contains `@ : / ? # &`.

> **Take the Session pooler, not the direct connection.** The direct host
> (`db.<ref>.supabase.co`) resolves to an IPv6 address only. On any network
> without IPv6 it fails before opening a socket, with
> `getaddrinfo failed` / `failed to resolve host`. The pooler host
> (`aws-0-<region>.pooler.supabase.com`) has IPv4 and works everywhere.
> Note the username differs too: `postgres.<project-ref>`, not `postgres`.

One hosted database means all five of us share the same data: P3's UI sees P1's
transactions without waiting for anyone to re-seed.

Tables are created on first boot, in the `public` schema. Nothing else about the
app changes — no Supabase SDK, no client library, same SQLAlchemy models.

**Before anyone shares a key:** our API connects as the database owner and does
its own `user_id` filtering, so row-level security is not in play. Supabase also
auto-generates a REST API over these same tables. If anyone exposes the anon
key, enable RLS on every table first — without it that key reads everything.

<details>
<summary>Local Postgres or SQLite instead</summary>

```bash
brew install postgresql@17
pg_ctl -D /opt/homebrew/var/postgresql@17 -l /tmp/pg17.log start
createdb budgetx
# DATABASE_URL=postgresql+psycopg://localhost/budgetx
```

`brew services` is broken on this machine (an unrelated Homebrew bug), so start
the server with `pg_ctl` directly; `pg_ctl -D ... stop` to shut it down.

Or skip Postgres entirely and work solo without touching the shared database:
`DATABASE_URL=sqlite:///local.db`. Everything runs and seeds itself.
</details>

### App

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in DATABASE_URL and GEMINI_API_KEY

.venv/bin/uvicorn app.main:app --reload      # API + docs on :8000/docs
.venv/bin/streamlit run ui/app.py            # UI on :8501
```

The database seeds itself on first boot: one user, 10 categories, 3 finished
months plus the current month to date, 6 budgets and a goal.

### Resetting before a demo

```bash
# stop the API first — Ctrl-C the uvicorn terminal
.venv/bin/python -m app.seed
```

Two reasons this matters. Alerts fire **once per level per budget** — the
cooldown is a unique constraint — so once the ₹800 dining transaction has
triggered `PROJECTED_OVERRUN`, it will never fire again and the next save looks
silently broken. And reseeding while the server is running deadlocks: `DROP
TABLE` needs a lock the live connections hold. If you see `DeadlockDetected`,
that is all it is.

Reseeding wipes the shared database. Tell the team before you run it.

## Testing

```bash
.venv/bin/python -m pytest        # 44 tests, ~0.5s
```

They run on in-memory SQLite, so they need no server. Nothing in the models or
queries is dialect-specific — that portability is the point of the ORM layer,
and the suite proves it on every run. Coverage is the deterministic layer:
alert thresholds and the 2% overrun margin, the cooldown, pace projection edge
cases, goal feasibility, input validation, and Supabase connection handling.
LLM *wording* is deliberately not tested — it is unstable by design.

## What runs without an API key

Every LLM call degrades to a deterministic fallback: keyword tool routing, the
pre-computed alert sentence, keyword theme clustering, average-based budget
suggestions. No endpoint returns an error because the provider is down — the
answer is just less eloquent, and the degradation is recorded rather than hidden.

Successful responses are cached to disk by `sha256(model|temperature|prompt)`,
so a rate limit mid-demo replays instead of failing. Fallbacks are deliberately
**not** cached — caching one would freeze a momentary outage into every later run.

Model is `gemini-2.5-flash` with reasoning disabled (`thinkingBudget: 0`): every
task here restates given facts or returns JSON, so thinking only consumed the
output budget and truncated replies.

## Layout

```
app/
  __init__.py     loads .env before any module reads it
  db.py models.py schemas.py main.py seed.py
  services/       spending, budgets, alerts, goals, simulate — no LLM imports
  ai/             llm_client (the only provider chokepoint), redact, narrate, router
  ai/prompts/     versioned .txt files
  routers/        transactions, budgets, goals, insights, chat, audit
ui/app.py         Streamlit — five pages
tests/            the deterministic layer
```

## Endpoints

| Endpoint | Notes |
|---|---|
| `POST /transactions` | insert + alert + reallocation in one round trip |
| `GET /transactions?month=` | newest first |
| `GET /categories` | 10 seeded |
| `POST /categories` · `DELETE /categories/{id}` | delete is refused if the category is in use, or if it would drop the count below 5 |
| `POST /budgets` · `GET /budgets?month=` | each with spent, pct_used, projected_total, `projected_over`, days_left |
| `POST /budgets/suggest` | limits proposed from actual history, clamped server-side |
| `POST /goals` · `GET /goals` · `GET /goals/{id}/plan` | feasibility, monthly required, gap, required cut |
| `GET /insights/summary?month=` | totals, by category, month-over-month on equal days |
| `GET /insights/themes` | behavioural clustering over notes only |
| `POST /simulate` | cut a category, watch the goal date move |
| `GET /alerts?unread=true` | |
| `POST /chat` | tool-calling entry point |
| `GET /audit/llm-calls` | every payload that left the machine — **API only, no UI page** |

`projected_over` is `0.0` unless a budget is projected past its limit by more
than 2%. Never recompute it client-side: a category spending exactly on pace
lands within floating-point dust of its limit, and the margin is what stops
"₹0.01 over" being reported as news.

## Not built yet

- Note enrichment (`ai/enrich.py`) and `MerchantHint` category autofill
- Goal contribution ledger (the denormalised `saved_amount` total is there)
- Marking an alert read; adding to a goal's `saved_amount`
- `docs/BRD.md`, `docs/HLD.md` — the largest remaining piece
