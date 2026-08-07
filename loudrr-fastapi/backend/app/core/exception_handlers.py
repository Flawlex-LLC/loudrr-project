from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.errors import DomainError

def register_exception_handlers(app: FastAPI) -> None:
    """wire one handler per error category. call once from main.py"""
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError):
    # spec §4.5 — every failure is {"error": "..."} (NOT "detail")
    # the frontend's TypeScript types read err.error, so this MUST match
        return JSONResponse(
            status_code=exc.status_code,
            content={'error': str(exc)},
            )