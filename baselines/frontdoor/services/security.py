import hmac
import time
from collections import defaultdict, deque
from contextvars import ContextVar

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from backend.config import auth_mode, service_secret
from backend.delegation import verify_delegation
from backend.store import Store

principal = ContextVar("service_principal", default=None)
delegation = ContextVar("service_delegation", default="")


class ServiceAuth(BaseHTTPMiddleware):
    def __init__(self, app, audience):
        super().__init__(app)
        self.audience = audience
        self.windows = defaultdict(deque)

    async def dispatch(self, request, call_next):
        if request.url.path in {"/health", "/.well-known/agent-card.json"}:
            return await call_next(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(token, service_secret()):
            return JSONResponse({"detail": "Service authentication required"}, status_code=401)
        assertion = request.headers.get("x-frontdoor-delegation", "")
        try:
            if assertion:
                identity = verify_delegation(assertion, self.audience, Store())
                if auth_mode() == "entra" and identity.get("auth_mode") != "entra":
                    raise HTTPException(401, "Entra delegation required")
            elif auth_mode() == "mock":
                identity = Store().user(request.headers.get("x-demo-user", ""))
                if identity["id"].startswith("entra:"):
                    raise HTTPException(401, "Verified delegation required")
            else:
                raise HTTPException(401, "Signed user delegation required")
        except HTTPException as error:
            return JSONResponse({"detail": error.detail}, status_code=error.status_code)
        key = identity["id"]
        window = self.windows[key]
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= 60:
            return JSONResponse({"detail": "Gateway quota exceeded"}, status_code=429)
        window.append(now)
        identity_token = principal.set(identity)
        delegation_token = delegation.set(assertion)
        try:
            return await call_next(request)
        finally:
            delegation.reset(delegation_token)
            principal.reset(identity_token)


def require_permission(user_id, permission):
    user = principal.get()
    if not user or user["id"] != user_id:
        raise HTTPException(403, "User does not match verified delegation")
    if permission not in user["permissions"]:
        raise HTTPException(403, "User not permitted")
    return user


def service_user(request: Request, permission):
    user = principal.get()
    return require_permission(user["id"] if user else "", permission)
