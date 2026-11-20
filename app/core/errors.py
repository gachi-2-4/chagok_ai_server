import logging
from contextlib import contextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("chagok")


class AppError(Exception):
    """명세의 에러 응답 { code, message, ... } 를 만드는 예외."""

    def __init__(self, status: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra

    def body(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


@contextmanager
def fail_as(code: str, message: str):
    """예상하지 못한 오류를 기능별 500 에러로 바꿉니다."""
    try:
        yield
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected error: %s", exc)
        raise AppError(500, code, message) from exc


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query", "path", "form"))
        message = f"{field} 값이 올바르지 않습니다." if field else "요청 형식이 올바르지 않습니다."
        return JSONResponse(status_code=400, content={"code": "INVALID_REQUEST", "message": message})

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        logger.exception("unhandled: %s", exc)
        return JSONResponse(status_code=500, content={"code": "INTERNAL_ERROR", "message": "서버 오류가 발생했습니다."})
