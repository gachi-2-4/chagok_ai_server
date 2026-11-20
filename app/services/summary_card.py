"""쉬운말 요약 카드. 값은 새로 만들지 않고 분석 결과(fields, parties)에서만 가져옵니다."""

from typing import Literal

from pydantic import BaseModel

from app.clients.backend import BackendClient
from app.clients.llm import LLMClient
from app.schemas.api import SummaryCardResponse, SummaryItem, SummarySection
from app.services.cache import TTLCache
from app.services.documents import display_value, fields_by_key, fields_for_prompt, require_analyzed
from app.utils.text import format_date_dot, mask_address, mask_name

SECTION_TITLES = {
    "LEASE_CONTRACT": ("계약 개요", "임대인, 임차인, 소재지, 보증금, 월세, 계약기간"),
    "EMPLOYMENT_CONTRACT": ("근로 개요", "근무처, 근무기간, 근무시간, 시급·월급, 급여일"),
    "SERVICE_CONTRACT": ("계약 개요", "계약 상대, 서비스, 금액, 계약기간, 해지 조건"),
    "BILL": ("청구 개요", "청구처, 청구금액, 납부기한, 납부방법"),
    "NOTICE": ("안내 개요", "대상, 일시, 장소, 준비물"),
    "ADVERTISEMENT": ("광고 개요", "광고주, 상품, 광고 가격, 혜택 기간"),
    "ETC": ("개요", "핵심 항목"),
}
CATEGORY_LABEL = {
    "LEASE_CONTRACT": "집 · 임대",
    "EMPLOYMENT_CONTRACT": "일 · 근로",
    "SERVICE_CONTRACT": "생활 · 계약",
    "BILL": "생활 · 고지서",
    "NOTICE": "생활 · 안내",
    "ADVERTISEMENT": "광고",
    "ETC": "기타",
}


class _KV(BaseModel):
    label: str
    source: Literal["FIELD", "PERIOD", "PARTY"]
    fieldKeys: list[str]
    partyRole: str | None


class _Check(BaseModel):
    checkKey: str
    text: str
    done: bool


class _LLMCard(BaseModel):
    title: str
    oneLineSummary: str
    keyValues: list[_KV]
    specialTerms: list[str]
    checklist: list[_Check]


_INSTRUCTIONS = """서류를 'AI 정리본' 카드로 짧게 정리한다. 말을 풀어 쓰지 말고 중요 쟁점만 고른다.
- keyValues: 개요에 넣을 항목. 값은 쓰지 말고 출처만 고른다.
  - FIELD: fieldKeys에 [항목] 목록의 key 1개
  - PERIOD: 기간. fieldKeys에 [시작 날짜 key, 끝 날짜 key]
  - PARTY: 당사자. partyRole에 [당사자] 목록의 role
  - label은 6자 이하
- specialTerms: 일반 조항이 아닌 이 서류만의 특별 조건, 최대 5개, 한 줄 25자 이하
- checklist: 사용자가 확인하거나 해야 할 일 (예: 확정일자, 전입신고), 최대 4개, 25자 이하.
  checkKey는 영어 camelCase. 서류에서 이미 했다는 것이 확인되면 done: true
- oneLineSummary: 핵심 금액 + 기간 + 꼭 해야 할 행동 1개, 2문장 이하, 금액은 '2억 3천만 원'처럼 읽기 쉽게
- title: 서류 제목"""


class SummaryCardService:
    def __init__(self, be: BackendClient, llm: LLMClient):
        self.be = be
        self.llm = llm
        self.cache = TTLCache(ttl_seconds=6 * 3600)

    async def build(self, document_id: int, user_id: int) -> SummaryCardResponse:
        doc = require_analyzed(await self.be.get_document(document_id, user_id))
        cache_key = (document_id, doc.get("updatedAt"))
        cached = self.cache.get(cache_key)
        if cached:
            return cached

        doc_type = doc.get("docType") or "ETC"
        section_title, wanted = SECTION_TITLES.get(doc_type, SECTION_TITLES["ETC"])
        parties = doc.get("parties") or []
        content = (
            f"[서류 종류] {doc_type}\n[개요에 넣을 항목] {wanted}\n[제목] {doc.get('title')}\n[요약] {doc.get('summary')}\n"
            f"[당사자] {', '.join(p.get('role', '') for p in parties) or '없음'}\n[항목]\n{fields_for_prompt(doc)}\n\n"
            f"[원문 일부]\n{(doc.get('fullText') or '')[:6000]}"
        )
        card = await self.llm.parse(_LLMCard, instructions=_INSTRUCTIONS, content=content, light=True, max_output_tokens=1200)

        fields = fields_by_key(doc)
        items: list[SummaryItem] = []
        for kv in card.keyValues:
            value, key = self._value(kv, fields, parties)
            if value:
                items.append(SummaryItem(label=kv.label[:6], value=value, fieldKey=key))

        sections = []
        if items:
            sections.append(SummarySection(type="KEY_VALUE", title=section_title, items=items))
        if card.specialTerms:
            sections.append(SummarySection(type="BULLET", title="주요 특약", items=[SummaryItem(text=t[:25]) for t in card.specialTerms[:5]]))
        if card.checklist:
            sections.append(
                SummarySection(
                    type="CHECKLIST",
                    title="체크",
                    items=[SummaryItem(checkKey=c.checkKey, text=c.text[:25], done=c.done) for c in card.checklist[:4]],
                )
            )

        result = SummaryCardResponse(
            documentId=document_id,
            category=CATEGORY_LABEL.get(doc_type, "기타"),
            title=card.title or doc.get("title") or "",
            oneLineSummary=card.oneLineSummary,
            sections=sections,
        )
        self.cache.set(cache_key, result)
        return result

    @staticmethod
    def _value(kv: _KV, fields: dict[str, dict], parties: list[dict]) -> tuple[str | None, str | None]:
        if kv.source == "PARTY":
            party = next((p for p in parties if p.get("role") == kv.partyRole), None)
            return (mask_name(party.get("name")) if party else None), None
        if kv.source == "PERIOD" and len(kv.fieldKeys) >= 2:
            start, end = fields.get(kv.fieldKeys[0]), fields.get(kv.fieldKeys[1])
            if start and end and start.get("value") and end.get("value"):
                return f"{format_date_dot(str(start['value']))} – {format_date_dot(str(end['value']))}", None
            return None, None
        if kv.fieldKeys and kv.fieldKeys[0] in fields:
            field = fields[kv.fieldKeys[0]]
            value = display_value(field)
            if "address" in field["key"].lower():
                value = mask_address(value)
            return value or None, field["key"]
        return None, None
