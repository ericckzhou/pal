"""S4/S5: Host and Origin allowlist, CSRF header, strict security headers."""

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-pal-request"
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def install(app: FastAPI, allowed_hosts: tuple[str, ...], allowed_origins: frozenset[str]) -> None:
    hosts = {h.lower() for h in allowed_hosts}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host", "").lower() not in hosts:
            return PlainTextResponse("Unknown host", status_code=400)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in allowed_origins:
                return PlainTextResponse("Cross-origin request refused", status_code=403)
            if request.headers.get(CSRF_HEADER) != "1":
                return PlainTextResponse("Missing request header", status_code=403)
        response = await call_next(request)
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
