import asyncio

from fastapi import APIRouter, Depends, Path

from app.core.deps import Container, get_container
from app.core.errors import fail_as
from app.core.security import require_api_key
from app.schemas.api import ComparisonRequest, ComparisonResponse, InquiryRequest, InquiryResponse

router = APIRouter(prefix="/ai/comparisons", tags=["COMPARISON"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=ComparisonResponse, summary="서류 비교")
async def compare(body: ComparisonRequest, c: Container = Depends(get_container)):
    with fail_as("AI_COMPARE_FAILED", "서류 비교 중 오류가 발생했습니다."):
        return await asyncio.wait_for(c.comparison.compare(body), timeout=c.settings.compare_timeout_seconds)


@router.post("/{id}/inquiry-messages", response_model=InquiryResponse, status_code=201, summary="확인 요청 문자 생성")
async def inquiry(body: InquiryRequest, id: int = Path(..., description="BE 비교 ID"), c: Container = Depends(get_container)):
    with fail_as("AI_GENERATION_FAILED", "문자 생성 중 오류가 발생했습니다."):
        return await asyncio.wait_for(c.inquiry.create(id, body), timeout=20)
