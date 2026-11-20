"""확인 요청 문자. 비교 결과의 금액만 쓰고, 생성 후 코드로 금액을 대조합니다."""

import re

from pydantic import BaseModel

from app.clients.backend import BackendClient
from app.clients.llm import LLMClient
from app.core.errors import AppError
from app.schemas.api import InquiryMessage, InquiryRequest, InquiryResponse
from app.services.comparison import describe_items
from app.utils.text import cp949_length, mask_sensitive

SMS_LIMIT = 90
LMS_LIMIT = 2000
TONE_GUIDE = {
    "POLITE": "정중한 존댓말 (기본)",
    "FRIENDLY": "부드럽고 친근한 존댓말",
    "FORMAL": "격식 있는 공문체 존댓말",
}
_MONEY = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+원|[+-]?\d+원")


class _LLMMessage(BaseModel):
    text: str


_INSTRUCTIONS = """발급처(회사)에 보낼 확인 요청 문자를 쓴다. 사용자가 복사해서 직접 보낸다.
- 순서: 인사 → 무엇에 대한 문의인지(예: 11월 통신요금) → 차이 내용 → 요청(청구 항목·산정 근거 안내)
- 금액은 [비교 결과]에 있는 금액 표기를 그대로만 쓴다. 다른 금액을 만들지 않는다.
- 문단 사이에 빈 줄을 넣는다. 600자 이내. 이름·계좌번호·주민번호는 쓰지 않는다."""


def _allowed_amounts(comparison: dict) -> set[str]:
    allowed = set()
    for c in comparison.get("comparisons") or []:
        for part in ("base", "target", "difference"):
            value = (c.get(part) or {}).get("value") or ""
            allowed.update(m.lstrip("+-") for m in _MONEY.findall(value))
    highlight = comparison.get("highlight") or {}
    allowed.update(m.lstrip("+-") for m in _MONEY.findall(highlight.get("value") or ""))
    return allowed


def _amounts_ok(text: str, allowed: set[str]) -> bool:
    return all(m.lstrip("+-") in allowed for m in _MONEY.findall(text))


def _fallback(comparison: dict, subject: str) -> str:
    lines = ["안녕하세요.", f"{subject}에 대해 확인 부탁드립니다.", ""]
    for c in comparison.get("comparisons") or []:
        diff = (c.get("difference") or {}).get("value")
        if diff:
            base, target = c.get("base") or {}, c.get("target") or {}
            lines.append(f"{base.get('label')}상 {c.get('title')}은(는) {base.get('value')}인데, {target.get('label')}에는 {target.get('value')}(으)로 {diff} 차이가 있습니다.")
    lines += ["", "차이가 생긴 항목과 산정 근거를 알려 주실 수 있을까요?"]
    return "\n".join(lines)


class InquiryService:
    def __init__(self, be: BackendClient, llm: LLMClient):
        self.be = be
        self.llm = llm

    async def create(self, comparison_id: int, req: InquiryRequest) -> InquiryResponse:
        comparison = await self.be.get_comparison(comparison_id, req.userId)
        if comparison.get("status") == "MATCH" or not describe_items(comparison):
            raise AppError(422, "NO_DIFFERENCE", "차이가 없어 확인 요청 문자를 만들 필요가 없습니다.")

        recipient, subject = "", "청구 금액"
        target_id = comparison.get("targetDocumentId")
        if target_id:
            try:
                target = await self.be.get_document(int(target_id), req.userId)
                parties = target.get("parties") or []
                issuer = next((p for p in parties if p.get("role") in ("ISSUER", "PROVIDER")), None)
                recipient = issuer.get("name", "") if issuer else ""
                subject = target.get("title") or subject
            except AppError:
                pass  # 받는 사람 이름이 없어도 문자는 만들 수 있음

        facts = describe_items(comparison)
        allowed = _allowed_amounts(comparison)
        content = (
            f"[받는 곳] {recipient or '발급처'}\n[문의 대상] {subject}\n[어조] {TONE_GUIDE[req.tone]}\n"
            f"[비교 결과]\n{facts}\n[추가로 물어볼 내용] {req.extraRequest or '없음'}"
        )

        text = None
        for _ in range(2):  # 금액이 다르면 한 번 더 생성
            result = await self.llm.parse(_LLMMessage, instructions=_INSTRUCTIONS, content=content, light=True, max_output_tokens=700)
            if _amounts_ok(result.text, allowed):
                text = result.text
                break
        text = mask_sensitive((text or _fallback(comparison, subject)).strip())

        length = cp949_length(text)
        if length > LMS_LIMIT:
            while cp949_length(text) > LMS_LIMIT:
                text = text[:-10]
            length = cp949_length(text)
        return InquiryResponse(
            comparisonId=comparison_id,
            message=InquiryMessage(text=text, type="SMS" if length <= SMS_LIMIT else "LMS", byteLength=length),
            tone=req.tone,
        )
