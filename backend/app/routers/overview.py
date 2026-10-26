from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import Auth, read_access
from app.db import get_db
from app.services import metrics

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/overview")
def overview(db: Session = Depends(get_db), _: Auth = Depends(read_access)):
    return metrics.overview(db)
