"""문서 분석 전체 흐름.

파일 검증 → OCR → 종류 분류 → 항목 정규화·조건 추출 → 법적 위험 점검 → 관련도·중요도 → 응답
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import date

from app.clients.backend import BackendClient
from app.clients.llm import LLMClient
from app.core.config import Settings
from app.core.errors import AppError
from app.legal.checker import LegalInput, run_legal_check
from app.legal.repository import LawRepository
from app.ocr.extract import extract_document
from app.schemas.analysis import AnalysisResponse
from app.services.analysis import extract as ex
from app.services.analysis.classify import classify
from app.services.analysis.priority import build_priority
from app.services.analysis.relevance import judge_relevance

logger = logging.getLogger("chagok.analysis")


@dataclass
class AnalysisRequest:
    document_id: int
    user_id: int
    file_bytes: bytes
    extension: str
    doc_type_hint: str | None
    ocr_result: str | None
    base_date: date
    legal_check: bool


async def _fetch_user(be: BackendClient, user_id: int, warnings: list[str]) -> dict | None:
    try:
        return await be.get_user(user_id)
    except AppError as err:
        if err.status == 404:
            raise
        warnings.append("USER_LOOKUP_FAILED")  # 사용자 정보가 없어도 분석은 계속
        return None


async def analyze(req: AnalysisRequest, settings: Settings, be: BackendClient, llm: LLMClient, laws: LawRepository) -> AnalysisResponse:
    warnings: list[str] = []

    # 사용자 정보 조회와 OCR을 동시에
    user_task = asyncio.create_task(_fetch_user(be, req.user_id, warnings))
    document = await asyncio.to_thread(extract_document, req.file_bytes, req.extension, req.ocr_result, settings.max_pdf_pages)

    classification = await classify(document.full_text, req.doc_type_hint, llm)
    raw = await ex.extract(document, classification.docType, req.base_date, llm)
    fields = ex.build_fields(raw, document, settings.review_confidence)
    keys = {f.key for f in fields}
    conditions = ex.build_conditions(raw, fields)
    events = ex.build_events(raw, keys, conditions)
    parties = ex.build_parties(raw)

    user = await user_task
    legal = None
    if req.legal_check:
        legal, legal_warnings = await run_legal_check(
            LegalInput(classification.docType, classification.docCategory, document, fields, user, req.base_date), llm, laws
        )
        warnings += legal_warnings
        if user and not user.get("profile"):
            warnings.append("PROFILE_NOT_FOUND")
        elif user and user.get("profile") and user.get("birthDate") is None:
            warnings.append("BIRTH_DATE_MISSING")

    relevance = None
    try:
        relevance = await judge_relevance(user, classification.docType, raw.title, raw.summary, parties, req.base_date, llm)
    except Exception as exc:  # noqa: BLE001
        logger.warning("relevance failed: %s", exc)
        warnings.append("USER_RELEVANCE_FAILED")

    priority = build_priority(classification.docCategory, fields, events, legal, relevance, req.base_date)

    return AnalysisResponse(
        documentId=req.document_id,
        docType=classification.docType,
        docTypeConfidence=classification.confidence,
        docCategory=classification.docCategory,
        classificationReasons=classification.reasons,
        title=raw.title,
        summary=raw.summary,
        parties=parties,
        fields=fields,
        conditions=conditions,
        calendarEvents=events,
        needsReview=any(f.needsReview for f in fields),
        userRelevance=relevance,
        fullText=document.full_text,
        pageCount=document.page_count,
        legalCheck=legal,
        priority=priority,
        warnings=warnings,
    )
