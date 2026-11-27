"""Domain exceptions mapped to consistent JSON error responses."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("intellicredit.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_failed"


class PreconditionFailed(AppError):
    status_code = 412
    code = "precondition_failed"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"


def _body(code: str, message: str, details: dict | list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(status_code=exc.status_code, content=_body("http_error", message), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        errors = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
            errors.append({"field": loc, "message": err.get("msg", "Invalid value")})
        first = errors[0] if errors else {"field": "", "message": "Invalid request"}
        message = f"{first['field']}: {first['message']}" if first["field"] else first["message"]
        return JSONResponse(status_code=422, content=_body("validation_failed", message, {"errors": errors}))

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, exc: IntegrityError):
        # The driver message can quote row values (e.g. "Key (email)=(...) already exists"): log the constraint only.
        log.warning("Integrity error (%s, constraint %s)", type(exc.orig).__name__,
                    getattr(getattr(exc.orig, "diag", None), "constraint_name", None))
        return JSONResponse(
            status_code=409,
            content=_body("conflict", "The record conflicts with existing data (duplicate or invalid reference)."),
        )

    @app.exception_handler(DataError)
    async def _data_error(_: Request, exc: DataError):
        # A value the database cannot store (NUL character in a query parameter, out-of-range number, ...).
        # Request validation rejects these first; this is the backstop. The value itself is never logged or echoed.
        log.warning("Value rejected by the database (%s, SQLSTATE %s)", type(exc.orig).__name__,
                    getattr(exc.orig, "sqlstate", None))
        return JSONResponse(status_code=422, content=_body(
            "invalid_value", "A submitted value cannot be stored (out of range or contains invalid characters)."))

    @app.exception_handler(OperationalError)
    async def _db_down(_: Request, exc: OperationalError):
        log.error("Database operational error: %s", exc)
        return JSONResponse(
            status_code=503,
            content=_body("database_unavailable", "The database is unavailable. Please retry shortly."),
        )
