import asyncio

from fastapi import APIRouter, Depends, Path

from app.core.deps import Container, get_container
from app.core.errors import fail_as
from app.core.security import require_api_key
from app.schemas.api import ChatRequest, ChatResponse, SuggestionsRequest, SuggestionsResponse

router = APIRouter(prefix="/ai", tags=["CHAT"], dependencies=[Depends(require_api_key)])


@router.post("/chats/{id}/answer", response_model=ChatResponse, summary="서류 기반 AI 채팅")
async def answer(body: ChatRequest, id: int = Path(..., description="BE 대화방 ID"), c: Container = Depends(get_container)):
    with fail_as("AI_GENERATION_FAILED", "답변 생성 중 오류가 발생했습니다."):
        return await asyncio.wait_for(
            c.chat.answer(id, body.userId, body.documentId, body.question), timeout=c.settings.chat_timeout_seconds
        )


@router.post("/chat/suggestions", response_model=SuggestionsResponse, summary="자주 묻는 질문 칩")
async def suggestions(body: SuggestionsRequest, c: Container = Depends(get_container)):
    with fail_as("AI_GENERATION_FAILED", "추천 질문 생성 중 오류가 발생했습니다."):
        return await asyncio.wait_for(c.suggestions.build(body.chatId, body.userId, body.documentId, body.count), timeout=10)
