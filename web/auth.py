"""
SEC-02 — JWT Authentication
Issues and validates Bearer tokens carrying scope claims.

Scopes:
  auditor  — full record including thought_log (internal tier)
  investor — structurally excludes thought_log, raw_output, llm_tokens

Token issuance:
  POST /api/auth/token  { "scope": "auditor"|"investor" }
  Returns { "access_token": "<jwt>", "token_type": "bearer", "scope": "..." }

  An investor token is open to anyone: it is the most restricted scope. An auditor
  token needs the admin key (`ACCOUNTABILITY_ADMIN_KEY`) in an `X-Admin-Key` header.
  With no admin key configured, auditor issuance is refused, never silently open.
  `ACCOUNTABILITY_INSECURE_DEV=1` restores the old open behaviour (and the old public
  signing secret) for a throwaway local session; it is never the default.

Validation:
  FastAPI dependency  require_scope()  is injected into protected routes.
  It reads Authorization: Bearer <token>, verifies the signature, and returns
  the decoded scope string.  Missing / invalid / expired tokens → 401.

Secret:
  `ACCOUNTABILITY_SECRET` if set. Otherwise a random per-process secret, so no
  token can be forged from the source code; tokens then stop working when the
  server restarts, and the review app fetches a new one.
"""

from __future__ import annotations

import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── Secret ─────────────────────────────────────────────────────────────────────

_DEV_SECRET = "dev-only-not-for-production-change-via-ACCOUNTABILITY_SECRET"
_ALGORITHM  = "HS256"
_TTL_HOURS  = 8

ScopeT = Literal["auditor", "investor"]

_bearer_scheme = HTTPBearer(auto_error=False)


_process_secret: str | None = None


def insecure_dev() -> bool:
    """`ACCOUNTABILITY_INSECURE_DEV=1`: the pre-hardening behaviour, for a throwaway local session."""
    return os.environ.get("ACCOUNTABILITY_INSECURE_DEV") == "1"


def _secret() -> str:
    global _process_secret
    configured = os.environ.get("ACCOUNTABILITY_SECRET")
    if configured:
        return configured
    if insecure_dev():
        return _DEV_SECRET
    if _process_secret is None:
        _process_secret = secrets.token_urlsafe(32)
    return _process_secret


# ── The admin key ──────────────────────────────────────────────────────────────

ADMIN_KEY_HEADER = "X-Admin-Key"


def admin_key() -> str | None:
    return os.environ.get("ACCOUNTABILITY_ADMIN_KEY") or None


def check_admin_key(provided: str | None) -> bool:
    """True if `provided` is the admin key (or the server is in insecure-dev mode)."""
    if insecure_dev():
        return True
    configured = admin_key()
    if configured is None or not provided:
        return False
    return hmac.compare_digest(provided.encode(), configured.encode())


def authorize_issue(scope: str, provided_key: str | None) -> None:
    """
    Raise 403 unless `provided_key` may be issued a `scope` token. Investor is open;
    auditor needs the admin key, and without a configured key it is refused.
    """
    if scope != "auditor" or insecure_dev():
        return
    if admin_key() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("Auditor tokens need an admin key, and none is configured. Set "
                    "ACCOUNTABILITY_ADMIN_KEY on the server (scripts/start-server.sh does it), "
                    f"then send it in the {ADMIN_KEY_HEADER} header."),
        )
    if not check_admin_key(provided_key):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"A valid {ADMIN_KEY_HEADER} is required for an auditor token.",
        )


# ── Issue ──────────────────────────────────────────────────────────────────────

def issue_token(scope: ScopeT) -> str:
    """
    Create a signed JWT carrying the requested scope. Whether the caller may have it
    is decided by `authorize_issue`, before this is called.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub":   "accountability-layer-user",
        "scope": scope,
        "iat":   now,
        "exp":   now + timedelta(hours=_TTL_HOURS),
    }
    return jwt.encode(payload, _secret(), algorithm=_ALGORITHM)


# ── Validate ───────────────────────────────────────────────────────────────────

def _decode(token: str) -> dict:
    try:
        return jwt.decode(token, _secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired. Request a new token from POST /api/auth/token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_scope(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> ScopeT:
    """
    FastAPI dependency.  Validates the Bearer token and returns the scope claim.
    Inject into any route that needs SEC-01 scope enforcement.

    Usage:
        @app.post("/api/chat")
        async def chat(scope: ScopeT = Depends(require_scope)):
            ...
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Include 'Authorization: Bearer <token>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = _decode(credentials.credentials)
    scope  = claims.get("scope")
    if scope not in ("auditor", "investor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Token carries unknown scope {scope!r}. Must be 'auditor' or 'investor'.",
        )
    return scope  # type: ignore[return-value]


def optional_scope(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> ScopeT | None:
    """
    Like require_scope, but a request with no token is allowed and yields None.

    For the read routes, which have never required a token (the legacy UI reads
    runs without one). The caller decides what None means; web/server.py falls back
    to the scope the run was stored under. A token that IS sent must be valid.
    """
    if credentials is None:
        return None
    return require_scope(credentials)


def require_auditor(scope: ScopeT = Depends(require_scope)) -> ScopeT:
    """A route only an auditor token may use (writes, replays, admin)."""
    if scope != "auditor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This route needs an auditor-scoped token.",
        )
    return scope
