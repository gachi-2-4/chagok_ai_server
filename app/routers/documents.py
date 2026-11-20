import asyncio
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile

from app.core.deps import Container, get_container
from app.core.errors import AppError, fail_as
from app.core.security import require_api_key
from app.ocr.extract import validate_file
from app.schemas.analysis import AnalysisResponse, DocType
from app.schemas.api import SummaryCardRequest, SummaryCardResponse
from app.services.analysis.pipeline import AnalysisRequest, analyze

router = APIRouter(prefix="/ai/documents", tags=["DOCUMENT"], dependencies=[Depends(require_api_key)])


@router.post("/{id}/analysis", response_model=AnalysisResponse, summary="문서 분석 (PDF·이미지 업로드)")
async def analyze_document(
    id: int = Path(..., description="BE가 만든 documentId"),
    file: UploadFile = File(...),
    userId: int = Form(...),
    docTypeHint: DocType | None = Form(default=None),
    ocrResult: str | None = Form(default=None),
    baseDate: date | None = Query(default=None),
    lang: str = Query(default="ko"),
    legalCheck: bool = Query(default=True),
    c: Container = Depends(get_container),
):
    data = await file.read()
    ext = validate_file(file.filename, len(data), c.settings.max_file_mb)
    req = AnalysisRequest(
        document_id=id,
        user_id=userId,
        file_bytes=data,
        extension=ext,
        doc_type_hint=docTypeHint,
        ocr_result=ocrResult,
        base_date=baseDate or date.today(),
        legal_check=legalCheck,
    )
    with fail_as("AI_ANALYSIS_FAILED", "문서 분석 중 오류가 발생했습니다."):
        try:
            return await asyncio.wait_for(analyze(req, c.settings, c.backend, c.llm, c.laws), timeout=c.settings.analysis_timeout_seconds)
        except TimeoutError as exc:
            raise AppError(500, "AI_ANALYSIS_FAILED", "문서 분석 시간이 초과되었습니다.") from exc


@router.post("/summary-card", response_model=SummaryCardResponse, summary="쉬운말 요약 카드")
async def summary_card(body: SummaryCardRequest, c: Container = Depends(get_container)):
    with fail_as("AI_GENERATION_FAILED", "요약 카드 생성 중 오류가 발생했습니다."):
        return await asyncio.wait_for(c.summary_card.build(body.documentId, body.userId), timeout=15)
