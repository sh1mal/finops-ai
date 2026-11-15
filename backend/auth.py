import os
import secrets
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .database import connect

router = APIRouter()
COOKIE = "finops_reviewer"
SESSION_SECONDS = 8 * 60 * 60


class ReviewerLogin(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def current_reviewer(request: Request) -> str | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT reviewer FROM reviewer_sessions WHERE token_hash = %s AND expires_at > now()",
            (digest(token),),
        ).fetchone()
    return row["reviewer"] if row else None


def require_reviewer(request: Request) -> str:
    reviewer = current_reviewer(request)
    if reviewer is None:
        raise HTTPException(403, "Sign in as a finance reviewer to continue.")
    return reviewer


@router.get("/session")
def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return {"reviewer": current_reviewer(request)}


@router.post("/session")
def sign_in(credentials: ReviewerLogin, request: Request, response: Response):
    password = os.environ.get("FINOPS_REVIEWER_PASSWORD", "")
    local_demo = (
        os.environ.get("FINOPS_LOCAL_DEMO_PASSWORD") == "1"
        and request.client is not None
        and request.client.host in {"127.0.0.1", "::1"}
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if not password or (len(password) < 16 and not local_demo):
        raise HTTPException(503, "Set FINOPS_REVIEWER_PASSWORD to at least 16 characters and restart the API.")
    if not secrets.compare_digest(digest(credentials.password), digest(password)):
        raise HTTPException(401, "Incorrect reviewer password.")
    reviewer = os.environ.get("FINOPS_REVIEWER_NAME", "Finance reviewer").strip() or "Finance reviewer"
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("DELETE FROM reviewer_sessions WHERE expires_at <= now()")
        conn.execute(
            "INSERT INTO reviewer_sessions VALUES (%s, %s, now() + %s * interval '1 second')",
            (digest(token), reviewer, SESSION_SECONDS),
        )
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                        secure=os.environ.get("FINOPS_SECURE_COOKIE") == "1")
    response.headers["Cache-Control"] = "no-store"
    return {"reviewer": reviewer}


@router.delete("/session")
def sign_out(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM reviewer_sessions WHERE token_hash = %s", (digest(token),))
    response.delete_cookie(COOKIE, httponly=True, samesite="strict",
                           secure=os.environ.get("FINOPS_SECURE_COOKIE") == "1")
    response.headers["Cache-Control"] = "no-store"
    return {"reviewer": None}
