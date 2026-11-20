import hmac

from fastapi import Header

from app.core.config import get_settings
from app.core.errors import AppError


async def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    expected = get_settings().ai_api_key
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise AppError(401, "INVALID_API_KEY", "API 키가 올바르지 않습니다.")
