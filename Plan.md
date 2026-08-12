# AI Financial Assistant — 3 Hour Build Plan

**Team size:** 5 · **Time budget:** 180 minutes · **Deliverable:** working prototype + BRD/HLD + 5-min demo

---

## 1. Problem & scope

**Problem statement:** Build an AI financial assistant that helps users understand spending patterns, create budgets, set savings goals, and receive personalized money-management suggestions.

**Our interpretation:** People don't fail at budgeting because they lack charts. They fail because feedback arrives *after* the money is gone. Our system intervenes at the moment of entry, and explains *why* spending happens — not just *how much*.

### In scope
- Manual transaction entry: amount + category dropdown + optional note
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
| DB | PostgreSQL | Workload is aggregation-heavy (`SUM` grouped by category over date ranges) and budget writes need ACID. Relational, not document. Real `NUMERIC` for money, and the scale path (connection pooling, read replicas, sharding by `user_id`) needs no rewrite. Test suite runs the same models on in-memory SQLite for speed |
| LLM | Gemini Flash (fallback: Groq) | Sub-second latency matters for live chat demo; native structured-output support; generous free tier |
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
  main.py                 # FastAPI app, CORS, startup seed
  db.py                   # engine, session dependency
  models.py               # SQLAlchemy models (all 8 entities)
  schemas.py              # Pydantic request/response models
  routers/
    transactions.py
    budgets.py
    goals.py
    insights.py
    chat.py
    audit.py
  services/               # DETERMINISTIC — no LLM imports allowed here
    spending.py           # category rollups, MoM delta
    budgets.py            # spend-vs-limit, pace projection
    alerts.py             # threshold evaluation + cooldown
    goals.py              # feasibility, monthly required
    simulate.py           # what-if recomputation
  ai/
    llm_client.py         # single chokepoint: retry, cache, audit
    redact.py             # PII stripping
    enrich.py             # note → tags/flags
    router.py             # question → tool + args
    narrate.py            # facts → prose
    prompts/              # versioned .txt files — judges ask to see these
  seed.py
ui/
  app.py                  # Streamlit
tests/
  test_alerts.py
  test_goals.py
  test_budgets.py
docs/
  BRD.md
  HLD.md
```

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
- **`saved_amount` denormalised on Goal** + a contribution ledger. Total makes the progress bar a single-row read; ledger makes the trend chart possible.
- **Indexes:** `(user_id, txn_date)` and `(user_id, category_id, txn_date)` — every dashboard query filters on exactly these.
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

```
POST   /transactions            {amount, category_id, note?, txn_date?}
                                → returns txn + alert (if triggered) in one round trip
GET    /transactions?month=
GET    /categories

POST   /budgets                 {category_id, limit_amount, period_start}
GET    /budgets?month=          → each with spent, pct_used, projected_total, days_left
POST   /budgets/suggest         → LLM proposes limits from actual history

POST   /goals                   {name, target_amount, target_date}
GET    /goals/{id}/plan         → feasibility + monthly_required + gap

GET    /insights/summary?month= → totals, by_category, mom_delta
GET    /insights/themes         → LLM behavioural clustering from notes
POST   /simulate                {category_id, pct_change} → new goal date

GET    /alerts?unread=true
POST   /chat                    {message, session_id} → tool-calling entry point
GET    /audit/llm-calls         → THE MONEY SHOT for criterion 8
```

---

## 6. LLM integration

### Where the LLM is used (and where it isn't)

| Task | LLM? | Why |
|---|---|---|
| Note enrichment (tags, reimbursable, one-off) | Yes, temp 0 | Semantic, cheap, cached |
| Chat tool routing | Yes, temp 0 | Returns JSON tool name only — never a number |
| Alert message wording | Yes, temp 0.7 | Receives computed facts, writes the sentence |
| Behavioural theme clustering | Yes, temp 0.4 | One call per month over notes only |
| Budget suggestion | Yes, temp 0.3 | Constrained to categories in actual history |
| **Any arithmetic** | **No** | Deterministic Python. Always. |
| Category autofill from repeat merchants | No | `MerchantHint` table — learned from user's own history |

### Reliability
- Single `llm_client.py` wrapper: retry-once-on-parse-failure with the error appended, then graceful degrade
- Tool names validated against a whitelist; unknown name → clarifying question, not a crash
- **Every response cached to disk** keyed by `sha256(prompt)`. If the API rate-limits during the demo, we replay from cache and nobody notices
- All Pydantic-enforced JSON output

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
days_elapsed = (txn_date - period_start).days + 1
days_total   = (period_end - period_start).days + 1
projected    = spent / days_elapsed * days_total

if   pct >= 100:                       level = BREACH
elif projected > limit:                level = PROJECTED_OVERRUN
elif pct >= 80:                        level = WARN
elif pct >= 50:                        level = INFO
else:                                  return None
```

**Order matters:** projected overrun is checked *before* the 80% threshold, so a user spending fast early gets warned at 40% rather than waiting for an arbitrary line.

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

**pytest coverage:**
- Alert thresholds: exactly 50 / 80 / 100%, and the cooldown (same level cannot fire twice)
- Pace projection on day 1 of a period (divide-by-zero guard)
- Goal feasibility: target date in the past, zero surplus, already-achieved goal
- Budget with no transactions; transaction with no budget
- Amount validation: zero, negative, absurdly large

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
| LLM API rate-limits mid-demo | Disk cache on every response; replay silently |
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
| 7 | Scalability, performance, testing | Async enrichment, caching, pytest on math layer, explicit scale roadmap |
| 8 | Security & PII | Redaction layer + live audit log demo |
| 9 | Demo & communication | Rehearsed fixed narrative, backup recording, dedicated presenter |
| 10 | Business value | Intervention timing as the ROI story, adoption path, future opportunities slide |
