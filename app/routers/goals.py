from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import DEMO_USER_ID, get_db
from app.models import Goal
from app.schemas import GoalIn, GoalOut, GoalPlan
from app.services import goals as goal_svc

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post("", response_model=GoalOut)
def create_goal(payload: GoalIn, db: Session = Depends(get_db)):
    goal = Goal(user_id=DEMO_USER_ID, **payload.model_dump())
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.get("", response_model=list[GoalOut])
def list_goals(db: Session = Depends(get_db)):
    return (
        db.execute(select(Goal).where(Goal.user_id == DEMO_USER_ID).order_by(Goal.target_date))
        .scalars()
        .all()
    )


@router.get("/{goal_id}/plan", response_model=GoalPlan)
def goal_plan(goal_id: int, db: Session = Depends(get_db)):
    goal = db.get(Goal, goal_id)
    if not goal:
        raise HTTPException(404, "goal not found")
    return GoalPlan(goal=GoalOut.model_validate(goal), **goal_svc.plan(db, goal))
