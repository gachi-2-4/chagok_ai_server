"""BE API 호출 (X-Internal-Key).

- 사용자 정보 조회  GET /user/{userId}
- 서류 상세 조회    GET /documents/{id}?userId=   (내부 키로 부르면 fullText 포함)
- 대화 메시지 조회  GET /chats/{id}/messages?userId=&size=
- 비교 결과 조회    GET /comparisons/{id}?userId=
"""

import logging

import httpx

from app.core.config import Settings
from app.core.errors import AppError

logger = logging.getLogger("chagok.backend")

LOOKUP_FAILED = ("BE_LOOKUP_FAILED", "서류·대화 정보를 불러오지 못했습니다.")


class BackendClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None):
        self._http = http or httpx.AsyncClient(
            base_url=settings.be_base_url,
            timeout=settings.be_timeout_seconds,
            headers={"X-Internal-Key": settings.be_internal_key},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict, not_found: tuple[str, str]) -> dict:
        try:
            res = await self._http.get(path, params={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as exc:
            logger.warning("BE request failed %s: %s", path, exc)
            raise AppError(502, *LOOKUP_FAILED) from exc

        if res.status_code == 404:
            raise AppError(404, *not_found)
        if res.status_code == 409:
            raise AppError(409, "DOCUMENT_NOT_READY", "서류 분석이 끝나지 않았습니다.")
        if res.status_code >= 400:
            logger.warning("BE %s -> %s %s", path, res.status_code, res.text[:200])
            raise AppError(502, *LOOKUP_FAILED)
        return res.json()

    async def get_user(self, user_id: int) -> dict:
        return await self._get(f"/user/{user_id}", {}, ("USER_NOT_FOUND", "사용자를 찾을 수 없습니다."))

    async def get_document(self, document_id: int, user_id: int) -> dict:
        return await self._get(
            f"/documents/{document_id}",
            {"userId": user_id},
            ("DOCUMENT_NOT_FOUND", "BE에서 서류를 찾을 수 없습니다."),
        )

    async def get_chat_messages(self, chat_id: int, user_id: int, size: int = 10, before_message_id: int | None = None) -> dict:
        return await self._get(
            f"/chats/{chat_id}/messages",
            {"userId": user_id, "size": size, "beforeMessageId": before_message_id},
            ("CHAT_NOT_FOUND", "BE에서 대화를 찾을 수 없습니다."),
        )

    async def get_comparison(self, comparison_id: int, user_id: int) -> dict:
        return await self._get(
            f"/comparisons/{comparison_id}",
            {"userId": user_id},
            ("COMPARISON_NOT_FOUND", "BE에서 비교 결과를 찾을 수 없습니다."),
        )
