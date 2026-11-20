"""자주 묻는 질문 칩. 문서 종류별 템플릿 + 서류 내용에 맞춘 질문 (LLM 호출 없음 → 비용 0)."""

from app.clients.backend import BackendClient
from app.schemas.api import Suggestion, SuggestionsResponse
from app.services.cache import TTLCache
from app.services.documents import fields_by_key, require_analyzed

# (조건 field key 또는 None, 질문, 카테고리)
TEMPLATES: dict[str, list[tuple[str | None, str, str]]] = {
    "LEASE_CONTRACT": [
        ("monthlyRent", "월세는 언제까지 내요?", "PAYMENT"),
        ("deposit", "보증금 언제 돌려받아?", "AMOUNT"),
        ("earlyTerminationPenalty", "중간에 나가면 위약금 있어?", "PENALTY"),
        ("contractEndDate", "계약 연장하려면 언제 말해?", "DEADLINE"),
        (None, "전입신고 꼭 해야 해?", "GENERAL"),
        ("maintenanceFee", "관리비는 얼마예요?", "AMOUNT"),
    ],
    "EMPLOYMENT_CONTRACT": [
        ("hourlyWage", "시급이 최저임금 이상이야?", "AMOUNT"),
        ("monthlySalary", "월급은 언제 들어와요?", "PAYMENT"),
        ("payDay", "월급날이 언제예요?", "PAYMENT"),
        ("weeklyHolidayPay", "주휴수당 받을 수 있어?", "AMOUNT"),
        ("workHoursPerDay", "하루 몇 시간 일해요?", "GENERAL"),
        ("contractEndDate", "계약은 언제 끝나요?", "DEADLINE"),
    ],
    "SERVICE_CONTRACT": [
        ("monthlyFee", "매달 얼마 내요?", "AMOUNT"),
        ("earlyTerminationPenalty", "해지하면 위약금 있어?", "PENALTY"),
        ("contractEndDate", "약정은 언제 끝나요?", "DEADLINE"),
        ("cancellationTerms", "해지는 어떻게 해요?", "GENERAL"),
    ],
    "BILL": [
        ("paymentDueDate", "언제까지 내야 하나요?", "DEADLINE"),
        ("billAmount", "이번 달 얼마 나왔어?", "AMOUNT"),
        ("lateFee", "늦게 내면 어떻게 돼?", "PENALTY"),
        ("paymentMethod", "어떻게 내면 돼요?", "PAYMENT"),
    ],
    "NOTICE": [
        ("eventDate", "언제 가야 해요?", "DEADLINE"),
        ("preparation", "뭘 준비해야 해?", "GENERAL"),
        ("applyDeadline", "신청 기한이 언제야?", "DEADLINE"),
    ],
    "ADVERTISEMENT": [
        (None, "이거 광고야, 고지서야?", "GENERAL"),
        ("promotedPrice", "정말 이 가격이야?", "AMOUNT"),
        (None, "꼭 답해야 하는 우편이야?", "GENERAL"),
    ],
    "ETC": [(None, "이 서류 핵심이 뭐야?", "GENERAL"), (None, "내가 해야 할 일 있어?", "GENERAL")],
}


class SuggestionService:
    def __init__(self, be: BackendClient):
        self.be = be
        self.cache = TTLCache(ttl_seconds=24 * 3600)

    async def build(self, chat_id: int, user_id: int, document_id: int, count: int) -> SuggestionsResponse:
        doc = require_analyzed(await self.be.get_document(document_id, user_id))
        cache_key = (document_id, doc.get("updatedAt"), count)
        suggestions = self.cache.get(cache_key)
        if suggestions is None:
            suggestions = self._pick(doc, count)
            self.cache.set(cache_key, suggestions)
        return SuggestionsResponse(chatId=chat_id, documentId=document_id, suggestions=suggestions)

    @staticmethod
    def _pick(doc: dict, count: int) -> list[Suggestion]:
        fields = fields_by_key(doc)
        templates = TEMPLATES.get(doc.get("docType") or "ETC", TEMPLATES["ETC"])
        # 서류에 있는 항목과 연결된 질문을 먼저, 그다음 일반 질문
        matched = [t for t in templates if t[0] and t[0] in fields]
        generic = [t for t in templates if t[0] is None]
        if (doc.get("legalCheck") or {}).get("issues"):
            generic.insert(0, (None, "법적으로 문제 될 부분 있어?", "GENERAL"))
        picked = (matched + generic + TEMPLATES["ETC"])[: max(1, count)]
        seen, result = set(), []
        for _, text, category in picked:
            if text not in seen:
                seen.add(text)
                result.append(Suggestion(text=text[:20], category=category))  # type: ignore[arg-type]
        return result
