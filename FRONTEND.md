# Frontend guide — P3

Everything here is verified against the running backend, not written from the
plan. If something below doesn't match reality, the doc is wrong — tell me.

**The backend is already built and seeded.** You are not blocked, and you don't
need mock JSON. A working Streamlit app already exists in [ui/app.py](ui/app.py)
covering all six screens — treat it as a starting point to improve, not a
finished thing.

---

## 1. Start in 60 seconds

```bash
git pull
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then paste the DATABASE_URL from the team chat

.venv/bin/uvicorn app.main:app --reload     # terminal 1 — API on :8000
.venv/bin/streamlit run ui/app.py           # terminal 2 — UI on :8501
```

Open <http://localhost:8000/docs> — that's live, clickable API documentation.
Use it to poke any endpoint before you write code against it.

We all share one Supabase database, so the data you see is the data everyone
sees. **This cuts both ways** — see §6 before you go clicking around.

---

## 2. What already exists

| Screen | Where | State |
|---|---|---|
| Dashboard | [ui/app.py:43](ui/app.py#L43) | metrics, category donut, cumulative trend, behavioural themes |
| Add transaction | [ui/app.py:97](ui/app.py#L97) | form + **live budget bar**, alert and reallocation on save |
| Budgets | [ui/app.py:134](ui/app.py#L134) | table, pace warnings, create, LLM suggestions |
| Goals | [ui/app.py:167](ui/app.py#L167) | progress, feasibility verdict, what-if slider |
| Chat | [ui/app.py:211](ui/app.py#L211) | chat history, tool used, raw facts |
| Audit log | [ui/app.py:230](ui/app.py#L230) | every payload sent to the LLM |

Two helpers you'll want: `api(method, path, **kwargs)` handles errors and stops
the script with a readable message when the backend is down, and `rupees(x)`
formats money. Use them rather than calling `httpx` directly.

### The live budget bar — don't break this

It's the single most-watched moment of the demo (step 2). The amount input is
deliberately **not** inside an `st.form`:

```python
amount = col_b.number_input("Amount (₹)", ...)     # outside a form
...
st.progress(min(after / budget["limit_amount"], 1.0), text=...)
```

Streamlit reruns the whole script on every widget change, so the bar moves as
the number is typed. Wrapping this in `st.form` batches input until submit and
the bar freezes — the effect dies. If you restructure the page, keep this out
of a form.

---

## 3. API reference

Base URL `http://localhost:8000`. All money is a **float** (rupees, 2dp), all
dates are **ISO strings** (`"2026-08-12"`). No auth, no headers needed.

### `POST /transactions` — the important one

Insert, threshold check and reallocation come back in **one** response. Don't
follow it with a `GET /alerts` — you already have everything.

```jsonc
// → {"amount": 800, "category_id": 3, "note": "ordered in again, too tired to cook"}
{
  "transaction": {
    "id": 146, "amount": 800.0, "category_id": 3,
    "note": "ordered in again, too tired to cook",
    "txn_date": "2026-08-12", "txn_type": "expense", "enrich_status": "pending"
  },
  "alert": {                                    // null when nothing crossed
    "id": 1, "budget_id": 1, "level": "PROJECTED_OVERRUN",
    "message": "Your Food & dining is projected to overrun. You've spent ₹6,281 of ₹12,000, but are projected to spend ₹16,227.",
    "spent_at_trigger": 6281.29, "limit_at_trigger": 12000.0,
    "projected_at_trigger": 16226.67, "is_read": false
  },
  "reallocation": "Food & dining is projected ₹4,227 over, but Transport is tracking ₹1,120 under — shift it?"
}
```

`alert` and `reallocation` are **often null** — that's normal, not an error.
Render the transaction confirmation regardless and the alert only when present.

`level` is one of `INFO` · `WARN` · `PROJECTED_OVERRUN` · `BREACH`. Style them
differently — `PROJECTED_OVERRUN` is our differentiator and deserves to look
distinct from a plain threshold cross. `message` is written by the LLM and its
length varies; don't build a layout that assumes one line.

### `GET /budgets?month=YYYY-MM`

```jsonc
[{
  "id": 1, "category_id": 3, "category_name": "Food & dining",
  "limit_amount": 12000.0, "period_start": "2026-08-01", "period_end": "2026-08-31",
  "spent": 5481.29, "pct_used": 45.7, "projected_total": 14160.0, "days_left": 19
}]
```

`projected_total > limit_amount` is the "on pace to overspend" condition — the
whole product argument. Show it, don't just show `pct_used`.

### `GET /insights/summary?month=YYYY-MM`

```jsonc
{
  "month": "2026-08", "total_spent": 22776.77, "total_income": 62000.0,
  "by_category": [
    {"category_id": 1, "category_name": "Rent & bills", "is_essential": true, "total": 6967.74},
    {"category_id": 3, "category_name": "Food & dining", "is_essential": false, "total": 5481.29}
  ],
  "mom_delta": -6860.07, "mom_delta_pct": -23.1
}
```

`by_category` is pre-sorted, biggest first. `is_essential` is there so you can
visually separate what's cuttable from what isn't — worth using in the donut.

`mom_delta` compares **the same number of days** into last month, not the whole
month. If you label it, say "vs last month, same days" — calling it "vs last
month" while showing a part-month figure is the kind of thing a judge catches.

### `GET /goals` and `GET /goals/{id}/plan`

```jsonc
{
  "goal": {"id": 1, "name": "Emergency fund", "target_amount": 60000.0,
           "target_date": "2027-02-01", "saved_amount": 12000.0},
  "months_left": 5.68, "remaining": 48000.0, "monthly_required": 8450.7,
  "avg_monthly_surplus": 4533.28, "gap": 3917.42, "feasible": false,
  "top_flex_category": "Food & dining",
  "verdict": "You'd need ₹8,451/month but you're averaging ₹4,533 — a ₹3,917 gap. Food & dining is your biggest flexible spend."
}
```

`verdict` is a ready-to-display sentence. `feasible` drives the colour.

### `POST /simulate`

```jsonc
// → {"category_id": 3, "pct_change": -25, "goal_id": 1}
{
  "category_name": "Food & dining", "monthly_delta": -3108.84, "new_avg_surplus": 7642.12,
  "goal_name": "Emergency fund", "old_target_date": "2027-02-01",
  "old_reach_date": "2027-06-30", "new_reach_date": "2027-02-19", "months_saved": 4.3
}
```

Compare `old_reach_date` against `new_reach_date` — those two are the same kind
of thing. `old_target_date` is the date the user *asked for*, a third thing;
putting it next to `new_reach_date` reads as a comparison and isn't one.
`new_reach_date` is `null` when the surplus is still ≤ 0 — handle that.

### `POST /chat`

```jsonc
// → {"message": "can I afford a 40k trip in December?", "session_id": "demo"}
{
  "reply": "You'd need ₹8,639/month to afford a ₹40,000 trip by December, but you're currently averaging a surplus of ₹4,533, leaving a gap of ₹4,106. To make this feasible, consider reducing your spending in categories like Food & dining, your biggest flexible expense.",
  "tool_used": "goal_feasibility",
  "facts": { /* the raw computed numbers the reply was written from */ }
}
```

Show `tool_used` somewhere small and put `facts` behind a collapsed `st.json`.
It looks like a debug detail; it's actually the proof that the number came from
Python and not from the model, which is criterion 2. Judges notice it.

`tool_used` is one of `spend_summary` · `budget_status` · `goal_feasibility` ·
`unknown`. On `unknown` the reply is a clarifying question — that's the designed
behaviour for an off-topic question, not a failure to render differently.

### The rest

| Endpoint | Returns |
|---|---|
| `GET /categories` | `[{id, name, is_essential}]` — 10 of them, cache it |
| `GET /transactions?month=` | newest first |
| `POST /budgets` | `{category_id, limit_amount}` → same shape as `GET /budgets`; **409** if that category already has a budget this month |
| `POST /budgets/suggest` | `{"suggestions": [{category_id, category, avg_monthly_spend, limit_amount, reason}]}` |
| `POST /goals` | `{name, target_amount, target_date}` |
| `GET /insights/themes` | `{"themes": [{theme, count, insight}], "note_count": 104}` — max 4 |
| `GET /alerts?unread=true` | alert objects, newest first |
| `GET /audit/llm-calls?limit=50` | newest first |

### Errors

`422` means Pydantic rejected the input — amount ≤ 0, amount > 10,000,000, note
over 280 chars, or a future `txn_date`. The body has the field and reason. Catch
these and show the message inline rather than letting the UI blow up; the
`api()` helper currently calls `st.error` and stops, which is fine for the demo
but ugly if a judge fat-fingers a form.

---

## 4. What's worth your time

Nothing is blocking. In rough order of demo value:

1. **Make `PROJECTED_OVERRUN` look urgent.** It's the differentiator and right
   now it renders as a generic `st.warning`. This is demo step 3.
2. **Colour the donut by `is_essential`** — essentials in a muted family,
   non-essentials in a hot one. Makes "what's cuttable" visible without a word
   of explanation.
3. **Reallocation as an actual suggestion card**, not an `st.info` line. Demo
   step 4.
4. **Sidebar badge with the unread alert count** from `GET /alerts?unread=true`.
5. **Style the empty states.** They exist but they're plain `st.info`. The
   seeded data means judges won't see them — unless someone filters to a month
   with no data on stage.
6. **Theme cards on the dashboard** — currently a bare list. Step 5 of the demo.

### Needs backend work — ask first, don't build around it

- **Marking an alert read.** `is_read` exists on the model but there's no
  endpoint to set it. If you want the badge to clear, ask for `POST /alerts/{id}/read`.
- **Adding to a goal's `saved_amount`.** No endpoint. Ask if the demo needs it.
- **Editing or deleting a transaction.** Not built. Probably not worth it.

---

## 5. Demo script → screen

Rehearse against this. Everything but step 2 runs on seeded data.

| # | Beat | Screen |
|---|---|---|
| 1 | "here's where your money goes" | Dashboard |
| 2 | type ₹800, Food, "too tired to cook" — **bar moves live** | Add transaction |
| 3 | alert fires in the same response | Add transaction |
| 4 | "transport is under, shift it?" | Add transaction |
| 5 | "11 of your 19 food entries mention being tired" | Dashboard themes |
| 6 | set a goal, ask the chatbot about a ₹40k trip | Goals → Chat |
| 7 | show the literal redacted payload | Audit log |

Step 7 is worth more than it looks. Open the entry whose `fields_stripped` says
`phone` — a seeded note contains a phone number and you can show it leaving as
`[PHONE_REDACTED]`. That single screen answers the security criterion better
than any slide.

---

## 6. Gotchas that will bite you

**Alerts fire once per level, ever.** `UniqueConstraint(budget_id, level)` is
the cooldown. Once you've tested the ₹800 transaction, `PROJECTED_OVERRUN` for
that budget has fired and **will not fire again** — the next one saves silently
with `"alert": null`. This looks exactly like a broken frontend and isn't.

Reset before every rehearsal:

```bash
.venv/bin/python -m app.seed        # drops everything, reseeds
```

That wipes the shared database, so tell the team before you run it.

**Working solo?** Point at a local database instead and leave the shared one
alone — set `DATABASE_URL=sqlite:///local.db` in your `.env`. Everything works;
you just won't see anyone else's data.

**The seeded month is shaped deliberately.** Dining sits at ~46% on a day when
only ~39% of the month has elapsed, and transport runs under. That's what makes
the pace alert fire before the 80% line and gives the reallocation something to
suggest. If you reseed and the numbers look different, that's the current date
moving, not a bug.

**The LLM can be slow or down.** `POST /chat`, `/insights/themes` and
`/budgets/suggest` make a live call — budget 1–3 seconds and show a spinner. If
the provider fails, the endpoint still returns a valid response built from a
deterministic fallback, so **you never need to handle an LLM error**. It just
gets less eloquent. Responses are cached to disk, so the second call is instant.

**Don't compute money in the frontend.** If you find yourself writing
`spent / limit * 100`, the backend already returns `pct_used`. The one exception
is the live budget bar, which must add the un-saved typed amount to `spent` —
that's a preview, not a stored figure.
