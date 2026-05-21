"""Security response headers + CSRF content-type guard (v3.6 Round-2).

Sector 7 Round-2 audit: the service serves a browser UI on
127.0.0.1 with no auth. ``OriginGuardMiddleware`` blocks cross-origin
requests, but two gaps remained:

1. **No CSRF defense by design.** A same-origin malicious page (or a
   plain ``<form>`` that sends no ``Origin`` header) could POST to a
   mutating ``/memory/*`` route. FastAPI happens to 422 a form body
   because the routes expect JSON — but that is accidental, not a
   guard. This middleware makes it explicit: a ``POST`` to
   ``/memory/*`` MUST carry ``Content-Type: application/json``. A
   browser ``<form>`` can only send ``x-www-form-urlencoded`` /
   ``multipart`` / ``text-plain`` — never ``application/json`` — so
   the form-POST CSRF vector is closed deterministically. Every
   legitimate programmatic client (curl, httpx, the inject hooks,
   the MCP HTTP delegation) already sends that header.

2. **No security headers.** No CSP, no ``X-Frame-Options``, no
   ``X-Content-Type-Options``. Any XSS that landed had zero
   defence-in-depth and every ``/ui`` page was frameable for
   clickjacking. The CSP confines ``default-src`` to ``'self'`` so
   even a landed XSS cannot exfiltrate to an external host;
   ``frame-ancestors 'none'`` + ``X-Frame-Options: DENY`` block
   clickjacking. ``'unsafe-inline'`` is required for script/style
   because every ``/ui`` page ships inline blocks — rewriting them
   to nonces is a larger change tracked separately.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)

_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": _CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # CSRF guard — POST to a mutating /memory/* route must be JSON.
        # Scoped to POST: a browser <form> can only issue GET/POST, so
        # PUT/PATCH/DELETE are not form-reachable and a bodyless DELETE
        # legitimately carries no Content-Type.
        if request.method == "POST" and request.url.path.startswith("/memory/"):
            ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if ctype != "application/json":
                return JSONResponse(
                    status_code=415,
                    content={
                        "error": "unsupported_media_type",
                        "detail": (
                            "POST /memory/* requires Content-Type: "
                            "application/json. This also blocks cross-site "
                            "form-POST (CSRF) — a browser form cannot send "
                            "that content-type."
                        ),
                    },
                )
        response = await call_next(request)
        # Decorate every response (incl. errors from inner middleware).
        # setdefault: never clobber a header a route set deliberately.
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
