"""Structured logging and per-request context.

Log records never include request bodies, uploaded document contents, tokens or secrets;
only method, path, status, duration, request id and (when known) user/organisation ids.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_REDACT_KEYS = ("password", "token", "secret", "api_key", "authorization", "cookie")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = request_id_var.get()
        if rid:
            payload["request_id"] = rid
        for key, value in getattr(record, "ctx", {}).items():
            if not any(r in key.lower() for r in _REDACT_KEYS):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Access logs are emitted by our middleware instead (without query strings).
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpcore", "pdfminer", "PIL", "rapidocr_onnxruntime", "RapidOCR"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


access_log = logging.getLogger("intellicredit.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        if not rid.isalnum() or len(rid) > 64:
            rid = uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            response.headers.setdefault("x-content-type-options", "nosniff")
            response.headers.setdefault("referrer-policy", "same-origin")
            response.headers.setdefault("x-frame-options", "DENY")
            return response
        finally:
            access_log.info(
                "request",
                extra={"ctx": {"method": request.method, "path": request.url.path, "status": status,
                               "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                               "client": request.client.host if request.client else None}},
            )
            request_id_var.reset(token)
