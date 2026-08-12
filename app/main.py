from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import audit, budgets, chat, goals, insights, transactions
from app.seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_if_empty()
    yield


app = FastAPI(
    title="AI BudgetX",
    description="Spending insight, pace-based budget alerts and goal planning. "
    "All money math is deterministic; the LLM narrates and routes only.",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # single-user prototype, no auth — see plan assumptions
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (transactions, budgets, goals, insights, chat, audit):
    app.include_router(module.router)


@app.get("/health")
def health():
    return {"status": "ok"}
