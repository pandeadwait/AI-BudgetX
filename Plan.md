# AI Financial Assistant — 3 Hour Build Plan

**Team size:** 5 · **Time budget:** 180 minutes · **Deliverable:** working prototype + BRD/HLD + 5-min demo

> **Build status.** The backend, AI layer, Streamlit UI, seed data and test
> suite are built and running against Supabase. [README.md](README.md) to run
> it, [FRONTEND.md](FRONTEND.md) for the API contract with real payloads.
> Anything below tagged **not built** is still open — the BRD and HLD are the
> largest remaining pieces.

---

## 1. Problem & scope

**Problem statement:** Build an AI financial assistant that helps users understand spending patterns, create budgets, set savings goals, and receive personalized money-management suggestions.

**Our interpretation:** People don't fail at budgeting because they lack charts. They fail because feedback arrives *after* the money is gone. Our system intervenes at the moment of entry, and explains *why* spending happens — not just *how much*.

### In scope
- Manual transaction entry: amount + category dropdown + optional note
- Category management: add custom categories, delete unused categories (min 5 categories enforced, transaction/budget dependency guards)
- Budget creation per category per month
- Real-time budget alerts with pace-based projection
- Savings goals with feasibility calculation
- Chatbot for recommendations and Q&A over the user's own data
- PII redaction layer + LLM audit log


### Explicitly out of scope (state these as assumptions)
- Authentication / multi-tenant security (single seeded user)
- Bank API integration or statement import
- Multi-currency, tax handling, investments
- Mobile app, production deployment, CI/CD

### Key assumptions
1. Single user session; `user_id = 1` hardcoded for the demo
2. INR only, monthly budget periods only
3. All amounts positive; direction carried by transaction type
4. Demo data pre-seeded so the dashboard is never empty on stage

---

## 2. Technology stack & justification

| Layer | Choice | Reasoning |
|---|---|---|
| API | FastAPI + Pydantic v2 | Async I/O hides LLM latency; schema validation gives a free contract; auto OpenAPI docs double as documentation deliverable |
| ORM | SQLAlchemy 2.0 | Declarative typed models; swaps SQLite → Postgres with one connection string change |
| DB | PostgreSQL, hosted on Supabase | Workload is aggregation-heavy (`SUM` grouped by category over date ranges) and budget writes need ACID. Relational, not document. Real `NUMERIC` for money, and the scale path (pooling, read replicas, sharding by `user_id`) needs no rewrite. Supabase gives the team one shared database in minutes with no ops work — SQLAlchemy talks to it directly, no vendor SDK, so it is a connection string we can move off. Tests run the same models on in-memory SQLite for speed |
| LLM | Gemini 2.5 Flash | Sub-second latency matters for a live chat demo; generous free tier. Reasoning is switched off (`thinkingBudget: 0`) — every task here restates given facts or returns JSON, so thinking only burned the output budget and truncated replies |
| UI | Streamlit | Python-native (no build step, no context switch), charts in 3 lines. Say out loud: *"chosen for prototype velocity; React/Next.js for production"* |
| Testing | pytest | Focused on the deterministic math layer, not LLM wording |

---

## 3. Architecture

### Core principle
**The LLM never touches money math, and never sees raw PII.**

Deterministic Python computes every number. The LLM only: (a) classifies notes, (b) routes chat questions to tools, (c) narrates computed facts. Between the AI layer and the provider sits a redaction gate that logs every outgoing payload.

### Layers
```
Streamlit UI
     ↓
FastAPI routers (Pydantic validation)
     ↓
     ├── Deterministic engine ──→ SQLite
     │   (all money math, no LLM)
     │
     └── AI orchestration ──→ Redaction + audit gate ──→ LLM provider
         (prompts, tool routing)
```

### Directory structure
```
app/
  __init__.py             # loads .env before any module reads it
  main.py                 # FastAPI app, CORS, startup seed
  db.py                   # engine, session dependency, Supabase URL handling
  models.py               # SQLAlchemy models (all 8 entities)
  schemas.py              # Pydantic request/response models
  seed.py                 # deterministic demo data
  routers/
    transactions.py       # + /categories
    budgets.py            # + /alerts, /budgets/suggest
    goals.py
    insights.py           # summary, themes, /simulate
    chat.py
    audit.py
  services/               # DETERMINISTIC — no LLM imports allowed here
    spending.py           # category rollups, MoM delta, surplus
    budgets.py            # spend-vs-limit, pace projection, overrun margin
    alerts.py             # threshold evaluation + cooldown + reallocation
    goals.py              # feasibility, monthly required
    simulate.py           # what-if recomputation
  ai/
    llm_client.py         # single chokepoint: retry, cache, audit, redact
    redact.py             # PII stripping
    router.py             # question → tool + args
    narrate.py            # facts → prose
    prompts/              # versioned .txt files — judges ask to see these
    enrich.py             # note → tags/flags        ** not built **
ui/
  app.py                  # Streamlit — six pages
tests/
  test_core.py            # 35 tests: the whole deterministic layer
docs/
  BRD.md                  ** not built **
  HLD.md                  ** not built **
```

`tests/` is one file rather than three: it runs in 0.2s and fits on two screens.
Split it per service when a rerun costs more than a glance.

---

## 4. Data model

### Entities

| Entity | Purpose |
|---|---|
| `User` | Root of ownership for everything |
| `Category` | Ten seeded dropdown options; `is_essential` drives "what's cuttable" |
| `Transaction` | Amount + category + optional note. The only thing the user types |
| `Budget` | Limit for one category, one period |
| `BudgetAlert` | A fired warning with a snapshot of state at trigger time |
| `Goal` | Savings target with a date |
| `ChatMessage` | Conversation history for multi-turn context |
| `LLMAuditLog` | Exact redacted payload sent to the model — the privacy proof |

### Relationships
- `User` 1—N `Transaction`, `Budget`, `Goal`, `ChatMessage`, `LLMAuditLog`
- `Category` 1—N `Transaction`, `Budget` ← *shared FK is what makes spend-vs-limit possible*
- `Budget` 1—N `BudgetAlert`
- `Transaction` 0..1—N `BudgetAlert` ← *lets the alert name the transaction that caused it*
- `Goal` deliberately has **no** FK to Category or Budget — it's a standalone target the recommendation logic reads across

### Design decisions to defend
- **Amount always positive**, direction in `txn_type`. Mixing signed amounts with a type column causes double-negation bugs under time pressure.
- **`period_start` / `period_end` as dates**, not a month string. Same table handles weekly budgets; every "spent so far" query is a plain `BETWEEN`.
- **`UniqueConstraint(budget_id, level)` on alerts** — this *is* the cooldown. `INSERT ... ON CONFLICT DO NOTHING` means the 80% alert fires exactly once.
- **Snapshot `spent_at_trigger` and `limit_at_trigger`** — alert history stays truthful even if the user later edits the budget.
- **`saved_amount` denormalised on Goal** + a contribution ledger *(ledger **not built** — the denormalised total is there and drives the progress bar; the ledger is what would make a savings trend chart possible)*.
- **Indexes:** `(user_id, txn_date)` and `(user_id, category_id, txn_date)` — every dashboard query filters on exactly these.
- **Category deletion safeguards:** Enforce a hard minimum floor of 5 categories to preserve system classification usability; block deletion if transactions or budgets are linked to prevent orphaned relational data.
- **`enrich_status` on Transaction** — LLM enrichment never blocks the insert. If the API rate-limits mid-demo, transactions still save and alerts still fire.


### Seed categories
```
Food & dining (non-essential)   Groceries (essential)
Transport (essential)            Rent & bills (essential)
Shopping (non-essential)         Entertainment (non-essential)
Health (essential)               Education (essential)
Subscriptions (non-essential)    Other (non-essential)
```

---

## 5. API contract — FREEZE AT T+15

Everyone codes against this. No changes after minute 15 without team agreement.

*Two additive changes since the freeze: `projected_over` on budget responses,
and `goal_id` plus both reach dates on `/simulate`. Nothing was removed or
renamed, so no client broke. [FRONTEND.md](FRONTEND.md) has the live payloads.*

```
POST   /transactions            {amount, category_id, note?, txn_date?}
                                → returns txn + alert (if triggered) in one round trip
GET    /transactions?month=
GET    /categories
POST   /categories              {name, is_essential?} → returns created category
DELETE /categories/{id}         → deletes category (requires >5 categories, 0 transactions/budgets)

POST   /budgets                 {category_id, limit_amount, period_start}
GET    /budgets?month=          → each with spent, pct_used, projected_total,
                                  projected_over, days_left
POST   /budgets/suggest         → LLM proposes limits from actual history

POST   /goals                   {name, target_amount, target_date}
GET    /goals/{id}/plan         → feasibility + monthly_required + gap

GET    /insights/summary?month= → totals, by_category, mom_delta
GET    /insights/themes         → LLM behavioural clustering from notes
POST   /simulate                {category_id, pct_change, goal_id?}
                                → old_reach_date vs new_reach_date, months_saved

GET    /alerts?unread=true
POST   /chat                    {message, session_id} → tool-calling entry point
GET    /audit/llm-calls         → THE MONEY SHOT for criterion 8
```

---

## 6. LLM integration

### Where the LLM is used (and where it isn't)

| Task | LLM? | Why |
|---|---|---|
| Note enrichment (tags, reimbursable, one-off) | Yes, temp 0 — **not built** | Semantic, cheap, cached. `enrich_status` stays `pending`; ingest never waited on it, which is why its absence breaks nothing |
| Chat tool routing | Yes, temp 0 | Returns JSON tool name only — never a number |
| Alert message wording | Yes, temp 0.7 | Receives computed facts, writes the sentence |
| Behavioural theme clustering | Yes, temp 0.4 | One call per month over notes only |
| Budget suggestion | Yes, temp 0.3 | Constrained to categories in actual history |
| **Any arithmetic** | **No** | Deterministic Python. Always. |
| Category autofill from repeat merchants | No — **not built** | `MerchantHint` table — learned from user's own history |

### Reliability
- Single `llm_client.py` wrapper: retry-once-on-parse-failure with the error appended, then graceful degrade. Nothing else in the codebase calls a provider, which is what makes redaction and audit impossible to forget
- Tool names validated against a whitelist; unknown name → clarifying question, not a crash
- **Every successful response cached to disk** keyed by `sha256(model|temp|prompt)`. If the API rate-limits during the demo, we replay from cache and nobody notices. Fallbacks are deliberately *not* cached — caching one would freeze a momentary outage into every later run and report it as a cache hit
- Every LLM output is parsed and then validated server-side before use: suggested budget limits are clamped to 50–150% of the category's real average and any invented category is dropped; tool names are whitelisted; themes are truncated to four. **The model's output is treated as untrusted input, not as an answer**
- Every endpoint that calls the LLM returns a valid response even when the provider is down, built from a deterministic fallback. There is no LLM error path for the frontend to handle

### Chat flow
```
"can I afford a ₹40k trip in March?"
  → LLM: {"tool": "goal_feasibility", "args": {"amount": 40000, "by": "2026-03-31"}}
  → engine: {"required_monthly": 5714, "avg_surplus": 4100, "gap": 1614,
             "top_flex_category": "dining"}
  → redact + log
  → LLM: "You'd need ₹5,714/month but you're averaging ₹4,100.
          Trimming dining by ~₹1,600 closes the gap."
```

---

## 7. Alert logic (the differentiator)

Most teams build "you crossed 80%." We build pace projection.

```python
days_elapsed = clamp((txn_date - period_start).days + 1, 1, days_total)
days_total   = (period_end - period_start).days + 1
projected    = spent / days_elapsed * days_total

if   pct >= 100:                          level = BREACH
elif projected > limit * 1.02:            level = PROJECTED_OVERRUN
elif pct >= 80:                           level = WARN
elif pct >= 50:                           level = INFO
else:                                     return None
```

**Order matters:** projected overrun is checked *before* the 80% threshold, so a user spending fast early gets warned at 40% rather than waiting for an arbitrary line.

**The 2% margin matters too.** A linear projection from a part-month is a rough instrument, and a category spending exactly on pace lands within floating-point dust of its limit — flipping over and under at random. Without the margin the system announced "projected ₹1,200.01 against a ₹1,200.00 limit" as if it were news. The margin is proportional rather than a flat rupee floor so it scales: ₹24 on a ₹1,200 budget, ₹240 on a ₹12,000 one. It lives in exactly one function, `budgets.overrun_amount()`, because the same comparison is needed by the alert engine, the reallocation finder, the chat tool and the UI — and four copies is four chances to reintroduce the false positive. `GET /budgets` returns the result as `projected_over` so no client recomputes it.

**`days_elapsed` is clamped** to at least 1 (day one of a period would divide by zero) and at most `days_total` (a back-dated transaction would otherwise project off the end of the month).

Alert text: *"You're at 62% of dining with 18 days left — at this pace you'll finish at ₹14,200 against a ₹12,000 budget."*

Plus **reallocation suggestions**: if dining is projected +₹2,200 over but transport is tracking ₹3,000 under, propose the shift. Deterministic to find, LLM to phrase.

---

## 8. Security, PII & privacy

| Control | Implementation |
|---|---|
| Storage minimisation | `email_hash` (sha256), no plaintext contact data |
| Redaction | Regex strip of phone / email / UPI handle / card / account patterns from notes before any LLM call |
| Payload minimisation | Enrichment prompt receives redacted note + category name only. Not amount, not date, not user id, not other transactions |
| Auditability | `LLMAuditLog` is a queryable table, not a log file — stores exact JSON sent, fields stripped, latency, cache-hit |
| Input validation | Pydantic bounds on amount, `max_length` on note, no-future-date validator |
| Injection resistance | User note is never concatenated into a system prompt; passed as a delimited variable |

**Demo move:** open `/audit/llm-calls` live and show the literal outgoing payload. This answers criterion 8 better than any slide.

---

## 9. Testing & quality

Test the deterministic layer hard, the LLM layer loosely.

**pytest coverage — 35 tests, 0.2s, no database server needed:**
- Alert thresholds at exactly 50 / 80 / 100%, and that a projected overrun outranks the 80% line while a breach outranks everything
- The 2% overrun margin: ₹0.01 over is silence, ₹201 over a ₹10,000 budget is an alert, and the boundary itself does not fire
- The cooldown — same level cannot fire twice, but an escalating level still can
- Pace projection on day 1 of a period (divide-by-zero guard) and on a back-dated transaction (must not project past period end)
- Goal feasibility: target date in the past, zero surplus, already-achieved goal
- Budget with no transactions; transaction with no budget; income never alerts
- Amount validation: zero, negative, absurdly large, future-dated
- Connection handling: a pasted Supabase URI gets the right driver and TLS, and the transaction pooler disables prepared statements

**For the LLM:** assert on *schema conformance* (valid JSON matching the Pydantic model), never on wording — wording is unstable and untestable in a hackathon.

---

## 10. Scalability roadmap (say this as future work, not as built)

- Shard Postgres by `user_id`; read replicas for the aggregate endpoints
- Note enrichment → Celery/RQ worker so ingest returns immediately
- Materialised monthly-summary table so the dashboard never scans raw transactions
- Redis in front of aggregate endpoints
- Semantic cache on chat queries; per-user LLM rate limiting

*Do not claim to have built any of this. Claiming you sharded anything in three hours reads as dishonest.*

---

## 11. Work split — 5 people

**Where each person actually stands:**

| | Built | Left |
|---|---|---|
| P1 | Schema, seed, all five service modules, every endpoint | Nothing blocking. `POST /alerts/{id}/read` and goal contributions if P3 asks |
| P2 | `llm_client`, prompts, tool router, narration, themes, budget suggestions | `enrich.py`; prompt tuning against real output |
| P3 | All six Streamlit pages working | Polish — [FRONTEND.md](FRONTEND.md) §4 ranks it by demo value |
| P4 | — | **BRD, HLD, deck. The largest remaining piece by far** |
| P5 | `redact.py`, audit wiring, 35 tests, fallbacks throughout | Backup demo recording; final integration pass |

### P1 — Data & Core Backend
Schema, SQLAlchemy models, seed generator (2–3 months of realistic transactions), all deterministic service functions, transaction/budget/goal/insight endpoints.
**Unblocks everyone — must ship seed data + `/transactions` by T+40 or the team stalls.**

### P2 — AI / LLM Engineer *(strongest Python person)*
`llm_client.py` with retry + cache + audit, prompt library in versioned files, note enrichment, chat tool router, narration, theme clustering. Owns all `ai/` code.

### P3 — Frontend & Demo Surface
Streamlit: entry form with **live budget bar** (updates as they type the amount), dashboard with category donut + trend line, budget manager, goal tracker, chat panel, audit viewer.
Works against hardcoded mock JSON from minute zero — never waits on P1.

### P4 — BRD / HLD & Pitch *(no code)*
BRD, HLD with component + sequence diagrams, tech-stack justification table, security section, and the deck: problem → personas → solution → architecture → impact/ROI → roadmap. **Presents at the end.**
This role covers criteria 1, 6, 9, 10 — 40% of the total score.

### P5 — Integration, Security & QA
`redact.py`, audit log wiring, error handling and fallbacks, pytest suite, final integration, and the **backup demo recording**.

---

## 12. Timeline

| Time | Milestone |
|---|---|
| 0:00–0:15 | Scope lock. API contract frozen. Repo skeleton pushed. API keys distributed to all 5 |
| 0:15–1:10 | Parallel build. P3 on mock JSON until P1 lands |
| 1:10–1:30 | **CHECKPOINT 1** — thin slice end-to-end: add transaction → alert fires → chart updates. If this fails, cut features now |
| 1:30–2:15 | Second wave: chat tool-calling, theme clustering, goal simulation, audit view |
| 2:15–2:30 | **FEATURE FREEZE.** Integration and bug fixes only. Nothing new |
| 2:30–2:45 | P5 records backup demo video. P4 finalises deck |
| 2:45–3:00 | Two full dry-runs of the 5-minute walkthrough |

---

## 13. Demo script — rehearse this exact path, don't improvise

1. **Dashboard** with pre-seeded history — *"here's where your money goes"*
2. **Add a transaction**: ₹800, Food, note *"ordered in again, too tired to cook"* — live budget bar moves as the amount is typed
3. **Alert fires instantly** in the same response — *"this pushes food to 92%; at this pace you'll finish ₹2,200 over"*
4. **Reallocation suggestion** — *"transport is ₹3,000 under, shift it?"*
5. **Behavioural themes** — *"11 of your 19 food entries mention being tired or late"*
6. **Set a goal**, ask the chatbot *"can I afford a ₹40k trip in March?"*
7. **Open the audit log** — show the literal redacted JSON that left the machine
8. Close on the roadmap slide

**Rule:** the demo runs on seeded data, not live typing, for everything except step 2.

---

## 14. Risk register

| Risk | Mitigation |
|---|---|
| LLM API rate-limits mid-demo | Disk cache on every successful response; replay silently. Every LLM endpoint also has a deterministic fallback, so the worst case is less eloquent prose, not an error |
| Model retired without warning | Happened during the build — `gemini-2.0-flash` started 404ing and every call silently fell back. The audit log made it visible in seconds; `/audit/llm-calls` is the first place to look if the demo sounds robotic |
| Merge conflicts | Single `main` branch, small frequent commits, clear file ownership per person |
| P3 blocked on backend | Mock JSON fixtures from minute zero |
| Scope creep after 2:15 | Hard feature freeze; anything new goes on the roadmap slide instead |
| Demo machine fails | P5's recorded video is the fallback |
| Empty-state ugliness | Seed data always present; empty states styled anyway |

---

## 15. Evaluation criteria coverage

| # | Criterion | Where we earn it |
|---|---|---|
| 1 | Problem understanding & innovation | Pace-based alerts, note-driven behavioural themes, intervention-at-entry framing |
| 2 | Prompt engineering & LLM utilisation | Versioned prompts, tool-calling router, Pydantic-enforced output, explicit "LLM does no math" boundary |
| 3 | Data modeling & DB design | 8 entities, indexes justified, cooldown-via-unique-constraint, denormalisation trade-offs |
| 4 | Architecture & code structure | Clean deterministic/AI split, single LLM chokepoint, layered directory structure |
| 5 | Tech stack & reasoning | Justification table with production-alternative named for each choice |
| 6 | Requirements & documentation | BRD + HLD owned by a dedicated person from minute zero |
| 7 | Scalability, performance, testing | Disk-cached LLM calls, indexes on the exact dashboard query shapes, 35 tests on the math layer, explicit scale roadmap. Enrichment is designed to be async (`enrich_status` never blocks ingest) but is not built — say "designed for", not "built" |
| 8 | Security & PII | Redaction layer + live audit log demo |
| 9 | Demo & communication | Rehearsed fixed narrative, backup recording, dedicated presenter |
| 10 | Business value | Intervention timing as the ROI story, adoption path, future opportunities slide |
