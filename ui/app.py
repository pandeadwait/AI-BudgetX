"""Streamlit demo surface. Chosen for prototype velocity; React/Next.js for production."""

import os
from datetime import date

import altair as alt
import httpx
import pandas as pd
import streamlit as st

API = os.getenv("API_BASE", "http://localhost:8000")

st.set_page_config(page_title="AI BudgetX", page_icon="₹", layout="wide")


def api(method: str, path: str, **kwargs):
    try:
        response = httpx.request(method, f"{API}{path}", timeout=30, **kwargs)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        st.error(f"API unreachable ({exc}). Is `uvicorn app.main:app` running?")
        st.stop()


@st.cache_data(ttl=60)
def categories():
    return api("GET", "/categories")


def rupees(value: float) -> str:
    return f"₹{value:,.0f}"


page = st.sidebar.radio(
    "Page",
    ["Dashboard", "Add transaction", "Budgets", "Goals", "Chat", "Audit log"],
    label_visibility="collapsed",
)
st.sidebar.caption("Single seeded user · INR · monthly periods")

# --- Dashboard ------------------------------------------------------------
if page == "Dashboard":
    st.title("Where your money goes")
    summary = api("GET", "/insights/summary")

    left, mid, right = st.columns(3)
    left.metric("Spent this month", rupees(summary["total_spent"]))
    mid.metric(
        "vs last month, same days",
        rupees(abs(summary["mom_delta"])),
        f"{summary['mom_delta_pct']:+.1f}%",
        delta_color="inverse",
    )
    right.metric("Income", rupees(summary["total_income"]))

    donut, trend = st.columns(2)
    if summary["by_category"]:
        frame = pd.DataFrame(summary["by_category"])
        donut.altair_chart(
            alt.Chart(frame)
            .mark_arc(innerRadius=60)
            .encode(
                theta="total:Q",
                color=alt.Color("category_name:N", title="category"),
                tooltip=["category_name", "total"],
            ),
            use_container_width=True,
        )
    else:
        donut.info("No spending recorded this month yet.")

    txns = api("GET", "/transactions")
    spend = [t for t in txns if t["txn_type"] == "expense"]
    if spend:
        daily = (
            pd.DataFrame(spend)
            .assign(txn_date=lambda d: pd.to_datetime(d["txn_date"]))
            .groupby("txn_date")["amount"]
            .sum()
            .cumsum()
        )
        trend.line_chart(daily, y_label="cumulative spend")
    else:
        trend.info("Add a transaction to see the trend.")

    st.subheader("Why the spending happens")
    themes = api("GET", "/insights/themes")
    if themes["themes"]:
        for theme in themes["themes"]:
            st.write(f"**{theme['theme']}** — {theme['count']} notes · {theme.get('insight', '')}")
        st.caption(f"Clustered from {themes['note_count']} notes. Amounts and dates never leave the machine.")
    else:
        st.info("No notes to cluster yet.")

# --- Add transaction ------------------------------------------------------
elif page == "Add transaction":
    st.title("Add a transaction")
    cats = {c["name"]: c["id"] for c in categories()}

    # Deliberately not inside st.form: Streamlit reruns on every keystroke, so
    # the budget bar below moves as the amount is typed. That is the whole point.
    col_a, col_b = st.columns(2)
    name = col_a.selectbox("Category", list(cats))
    amount = col_b.number_input("Amount (₹)", min_value=0.0, step=50.0, value=800.0)
    note = st.text_input("Note (optional)", placeholder="ordered in again, too tired to cook")

    budget = next((b for b in api("GET", "/budgets") if b["category_id"] == cats[name]), None)
    if budget:
        after = budget["spent"] + amount
        st.progress(
            min(after / budget["limit_amount"], 1.0),
            text=f"{name}: {rupees(after)} of {rupees(budget['limit_amount'])} "
            f"({after / budget['limit_amount'] * 100:.0f}%) · {budget['days_left']} days left",
        )
    else:
        st.caption(f"No budget set for {name}.")

    if st.button("Save", type="primary", disabled=amount <= 0):
        result = api(
            "POST",
            "/transactions",
            json={"amount": amount, "category_id": cats[name], "note": note or None},
        )
        st.success(f"Saved {rupees(result['transaction']['amount'])} to {name}.")
        if result["alert"]:
            st.warning(f"**{result['alert']['level'].replace('_', ' ').title()}** — {result['alert']['message']}")
        if result["reallocation"]:
            st.info(result["reallocation"])
        if not result["alert"]:
            st.caption("No threshold crossed.")

# --- Budgets --------------------------------------------------------------
elif page == "Budgets":
    st.title("Budgets")
    rows = api("GET", "/budgets")
    if rows:
        st.dataframe(
            pd.DataFrame(rows)[
                ["category_name", "spent", "limit_amount", "pct_used", "projected_total", "days_left"]
            ],
            hide_index=True,
            use_container_width=True,
        )
        for row in rows:
            if row["projected_over"]:
                st.warning(
                    f"{row['category_name']} is on pace to finish at "
                    f"{rupees(row['projected_total'])} against {rupees(row['limit_amount'])} "
                    f"— {rupees(row['projected_over'])} over."
                )
    else:
        st.info("No budgets for this month yet.")

    with st.expander("Set a budget"):
        cats = {c["name"]: c["id"] for c in categories()}
        name = st.selectbox("Category", list(cats), key="budget_cat")
        limit = st.number_input("Monthly limit (₹)", min_value=100.0, step=500.0, value=10_000.0)
        if st.button("Save budget"):
            api("POST", "/budgets", json={"category_id": cats[name], "limit_amount": limit})
            st.rerun()

    if st.button("Suggest limits from my history"):
        for item in api("POST", "/budgets/suggest")["suggestions"]:
            st.write(f"**{item['category']}** → {rupees(item['limit_amount'])} · {item.get('reason', '')}")

# --- Goals ----------------------------------------------------------------
elif page == "Goals":
    st.title("Savings goals")
    goals = api("GET", "/goals")

    for goal in goals:
        plan = api("GET", f"/goals/{goal['id']}/plan")
        st.subheader(goal["name"])
        st.progress(min(goal["saved_amount"] / goal["target_amount"], 1.0),
                    text=f"{rupees(goal['saved_amount'])} of {rupees(goal['target_amount'])} by {goal['target_date']}")
        st.write(("✅ " if plan["feasible"] else "⚠️ ") + plan["verdict"])

    with st.expander("Add a goal"):
        name = st.text_input("Name", "Goa trip")
        target = st.number_input("Target (₹)", min_value=1000.0, step=1000.0, value=40_000.0)
        when = st.date_input("By", value=date.today().replace(year=date.today().year + 1))
        if st.button("Create goal"):
            api("POST", "/goals", json={"name": name, "target_amount": target,
                                        "target_date": when.isoformat()})
            st.rerun()

    if goals:
        st.subheader("What if I cut back?")
        cats = {c["name"]: c["id"] for c in categories()}
        col_a, col_b = st.columns(2)
        cut_cat = col_a.selectbox("Category", list(cats), key="sim_cat")
        pct = col_b.slider("Change in spending (%)", -100, 100, -25)
        goal_names = {g["name"]: g["id"] for g in goals}
        target_goal = st.selectbox("Applied to goal", list(goal_names))
        result = api("POST", "/simulate", json={"category_id": cats[cut_cat], "pct_change": pct,
                                                "goal_id": goal_names[target_goal]})
        st.write(
            f"Monthly change: **{rupees(result['monthly_delta'])}** · "
            f"new average surplus **{rupees(result['new_avg_surplus'])}**"
        )
        if result["new_reach_date"]:
            st.success(
                f"'{result['goal_name']}' reached by **{result['new_reach_date']}** "
                f"instead of {result['old_reach_date']} "
                f"({result['months_saved']} months earlier)."
            )
        else:
            st.warning("At that surplus the goal is never reached.")

# --- Chat -----------------------------------------------------------------
elif page == "Chat":
    st.title("Ask about your money")
    st.caption("The model picks a tool and writes the sentence. Every number comes from Python.")

    for message in st.session_state.setdefault("chat", []):
        st.chat_message(message["role"]).write(message["content"])

    if question := st.chat_input("can I afford a ₹40k trip in March?"):
        st.session_state.chat.append({"role": "user", "content": question})
        st.chat_message("user").write(question)
        result = api("POST", "/chat", json={"message": question, "session_id": "demo"})
        st.session_state.chat.append({"role": "assistant", "content": result["reply"]})
        with st.chat_message("assistant"):
            st.write(result["reply"])
            st.caption(f"tool: `{result['tool_used']}`")
            if result["facts"]:
                st.json(result["facts"], expanded=False)

# --- Audit log ------------------------------------------------------------
else:
    st.title("What actually left the machine")
    st.caption("Every provider call, the exact redacted payload, and what was stripped.")
    rows = api("GET", "/audit/llm-calls")
    if not rows:
        st.info("No LLM calls yet.")
    for row in rows:
        stripped = row["fields_stripped"] or "nothing matched"
        with st.expander(
            f"#{row['id']} · {row['task']} · {row['latency_ms']}ms · "
            f"{'cache hit' if row['cache_hit'] else 'live call'} · stripped: {stripped}"
        ):
            st.code(row["prompt_sent"])
            st.caption(f"model: {row['model']}")
            st.text(row["response"])
