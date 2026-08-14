import uuid
from dataclasses import dataclass

from fastapi import Cookie, Depends, HTTPException

from atlas_engine.auth.session import verify_session_token
from atlas_engine.db import session_scope
from atlas_engine.db.models import User

SESSION_COOKIE_NAME = "atlas_session"


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    org_id: uuid.UUID
    role: str
    job_function: str | None


def get_current_user(atlas_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME)) -> CurrentUser:
    if atlas_session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = verify_session_token(atlas_session)
    if payload is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    with session_scope() as session:
        user = session.get(User, uuid.UUID(payload["user_id"]))
        if user is None:
            raise HTTPException(status_code=401, detail="User no longer exists")
        return CurrentUser(id=user.id, org_id=user.org_id, role=user.role, job_function=user.job_function)


def require_job_function(*allowed: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.job_function not in allowed and user.role not in ("owner", "admin"):
            raise HTTPException(status_code=403, detail=f"Requires job function: {', '.join(allowed)}")
        return user

    return dependency
