"""서류 기반 AI 채팅. 서류 원문(fullText) 전체 + 분석 결과 + 최근 대화를 LLM에 넣어 답합니다.
법 관련 질문일 때만 법령 DB를 검색(RAG)합니다."""

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel

from app.clients.backend import BackendClient
from app.clients.llm import LLMClient
from app.core.errors import AppError
from app.legal.checker import DISCLAIMER
from app.legal.repository import DOC_TYPE_TAGS, LawArticle, LawRepository
from app.schemas.api import ChatResponse, Citation
from app.services.cache import RateLimiter
from app.services.documents import fields_for_prompt, require_analyzed
from app.utils.text import best_window_similarity, calc_age, contains_loose

logger = logging.getLogger("chagok.chat")

LEGAL_WORDS = ["법", "불법", "합법", "위법", "무효", "소송", "신고", "고소", "권리", "의무", "위약금", "손해배상", "최저임금", "주휴", "해고",
               "부당", "보증금 반환", "돌려받", "확정일자", "전입", "계약 해지", "청약철회", "환불", "이자", "연체"]


class _LLMCitation(BaseModel):
    sourceType: Literal["DOCUMENT", "LAW"]
    page: int | None
    snippet: str
    lawRef: int | None


class _LLMAnswer(BaseModel):
    answer: str
    answerType: Literal["ANSWERED", "NOT_IN_DOCUMENT", "NEEDS_EXPERT"]
    citations: list[_LLMCitation]
    followUpQuestions: list[str]


_INSTRUCTIONS = """너는 사용자의 서류에 대해 답하는 도우미 '차곡'이다. 사용자는 법·계약을 잘 모르는 일반인이다.
- 2~3문장으로 짧게, 쉬운 존댓말(~요)로 답한다. 핵심 금액·날짜는 **굵게** 표시한다.
- [항목]의 값이 원문과 다르면 [항목] 값(사용자가 고친 값)을 따른다.
- 서류에 없는 내용은 지어내지 않는다. 서류에서 답을 찾을 수 없으면 answerType을 NOT_IN_DOCUMENT로 하고 그렇다고 말한다.
- 개별 사건의 법률 판단(소송 승패, 위법 여부 확정 등)이 필요하면 NEEDS_EXPERT로 하고 대한법률구조공단(132) 상담을 안내한다.
- citations: 답에 쓴 근거. DOCUMENT는 page([N페이지]의 N)와 원문 문장 그대로(snippet, 60자 이내).
  LAW는 [법령 조문] 목록의 번호를 lawRef에 넣는다. 목록에 없는 법은 인용하지 않는다.
- followUpQuestions: 이어서 물어볼 만한 질문 최대 2개, 20자 이하"""


def is_legal_question(question: str) -> bool:
    return any(word in question for word in LEGAL_WORDS)


class ChatService:
    def __init__(self, be: BackendClient, llm: LLMClient, laws: LawRepository, rate_per_minute: int):
        self.be = be
        self.llm = llm
        self.laws = laws
        self.limiter = RateLimiter(rate_per_minute)

    async def answer(self, chat_id: int, user_id: int, document_id: int, question: str) -> ChatResponse:
        if not self.limiter.allow(user_id):
            raise AppError(429, "RATE_LIMITED", "잠시 후 다시 시도해 주세요.")

        doc_res, history_res, user_res = await asyncio.gather(
            self.be.get_document(document_id, user_id),
            self.be.get_chat_messages(chat_id, user_id, size=10),
            self.be.get_user(user_id),
            return_exceptions=True,
        )
        if isinstance(doc_res, Exception):
            raise doc_res
        doc = require_analyzed(doc_res)
        history = history_res.get("messages", []) if isinstance(history_res, dict) else []
        user = user_res if isinstance(user_res, dict) else None

        laws: list[LawArticle] = []
        if is_legal_question(question) and self.laws.enabled:
            try:
                laws = await self.laws.search(question, DOC_TYPE_TAGS.get(doc.get("docType") or "ETC", ["civil"]), k=4)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat law search failed: %s", exc)

        content = self._content(doc, history, user, laws, question)
        result = await self.llm.parse(
            _LLMAnswer,
            instructions=_INSTRUCTIONS,
            content=content,
            max_output_tokens=800,
            cache_key=f"doc-{document_id}",
        )
        citations = self._citations(result.citations, doc, laws)
        has_law = any(c.sourceType == "LAW" for c in citations)
        return ChatResponse(
            answer=result.answer,
            answerType=result.answerType,
            citations=citations,
            disclaimer=DISCLAIMER if has_law or result.answerType == "NEEDS_EXPERT" or laws else None,
            followUpQuestions=[q[:20] for q in result.followUpQuestions[:2]],
        )

    def _content(self, doc: dict, history: list[dict], user: dict | None, laws: list[LawArticle], question: str) -> str:
        # 같은 서류를 여러 번 보낼 때 입력 캐시가 적용되도록 서류 부분을 맨 앞에 고정합니다.
        parts = [
            f"[서류] {doc.get('title')} ({doc.get('docType')})",
            f"[원문]\n{(doc.get('fullText') or '')[:30000]}",
            f"[항목]\n{fields_for_prompt(doc)}",
        ]
        consents = (user or {}).get("consents") or {}
        if user and consents.get("overseasTransfer") and user.get("profile"):
            from datetime import date

            age = calc_age(user.get("birthDate"), date.today())
            p = user["profile"]
            region = p.get("region") or {}
            parts.append(
                f"[사용자 조건] 나이: {age if age is not None else '알 수 없음'}, 지역: {region.get('sido', '')} {region.get('sigungu', '')}, "
                f"주거: {p.get('housingType')}, 복지: {p.get('welfareType')}"
            )
        if laws:
            parts.append("[법령 조문]\n" + "\n".join(f"#{i} {a.law_name} {a.article}: {a.content[:400]}" for i, a in enumerate(laws)))
        if history:
            lines = [f"{'사용자' if m.get('role') == 'USER' else '차곡'}: {m.get('content', '')[:300]}" for m in history[-10:]]
            parts.append("[지난 대화]\n" + "\n".join(lines))
        parts.append(f"[질문]\n{question}")
        return "\n\n".join(parts)

    @staticmethod
    def _citations(items: list[_LLMCitation], doc: dict, laws: list[LawArticle]) -> list[Citation]:
        full_text = doc.get("fullText") or ""
        fields = doc.get("fields") or []
        citations: list[Citation] = []
        for item in items[:3]:
            if item.sourceType == "LAW":
                if item.lawRef is None or not 0 <= item.lawRef < len(laws):
                    continue
                law = laws[item.lawRef]
                citations.append(Citation(sourceType="LAW", lawName=law.law_name, article=law.article, url=law.url, snippet=law.content[:120]))
                continue
            score = best_window_similarity(full_text, item.snippet)
            if score < 0.6:  # 원문에 없는 근거는 버림
                continue
            bbox, page = None, item.page
            for f in fields:
                loc = f.get("evidenceLocation") or {}
                if f.get("evidence") and (contains_loose(item.snippet, f["evidence"]) or contains_loose(f["evidence"], item.snippet)):
                    bbox, page = loc.get("bbox"), loc.get("page", page)
                    break
            citations.append(
                Citation(sourceType="DOCUMENT", documentId=doc.get("documentId"), page=page, bbox=bbox, snippet=item.snippet, score=round(score, 2))
            )
        return citations
