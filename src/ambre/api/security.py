"""Write protection for the local API.

The API has no session and no cookie: nothing identifies the caller. What a hostile web page can
do is send a request to localhost from the owner's browser, so the guard refuses what only such a
page sends. It does not authenticate anyone: a local process can still write (docs/SECURITY.md).
"""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
JSON_MEDIA_TYPE = "application/json"


def refusal(headers: Headers, allowed_origins: list[str]) -> tuple[int, str] | None:
    """Why a write is refused, or None.

    A browser always sends ``Origin`` on a cross-site POST, so a foreign or ``null`` value is
    refused and its absence means a non-browser client. ``application/json`` is required on every
    write, body or not: it is what forces a CORS preflight. FastAPI's own check only covers routes
    that read a body, which left ``/control/run-cycle`` open to a simple cross-site POST.
    """
    origin = headers.get("origin")
    if origin is not None and origin not in allowed_origins:
        return 403, "origin not allowed"
    media_type = headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type != JSON_MEDIA_TYPE:
        return 415, f"content type must be {JSON_MEDIA_TYPE}"
    return None


class WriteGuard:
    """Applies to every unsafe method, so a future write route is covered by default."""

    def __init__(self, app: ASGIApp, allowed_origins: list[str]) -> None:
        self.app = app
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            refused = refusal(Headers(scope=scope), self.allowed_origins)
            if refused is not None:
                status, detail = refused
                await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)
                return
        await self.app(scope, receive, send)
