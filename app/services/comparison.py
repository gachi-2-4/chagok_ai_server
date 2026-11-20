"""서류 비교. LLM은 항목 짝짓기와 이상 징후 문구만, 금액 계산은 코드로 합니다."""

import asyncio
from typing import Literal

from pydantic import BaseModel

from app.clients.backend import BackendClient
from app.clients.llm import LLMClient
from app.core.errors import AppError
from app.schemas.api import (
    CompareEvidence,
    CompareValue,
    ComparisonItem,
    ComparisonRequest,
    ComparisonResponse,
    Highlight,
    Notice,
)
from app.services.documents import fields_by_key, fields_for_prompt, require_analyzed
from app.utils.text import format_won

DISCLAIMER = "추정 결과이며 실제 과오납 여부는 발급처 확인이 필요해요."
HEADLINES = {
    "MISMATCH": "약속과 다른 금액이 있어요",
    "MATCH": "약속대로 청구됐어요",
    "NEEDS_CHECK": "비교할 항목을 찾기 어려워요",
}
TARGET_CATEGORY = {"BILL": "BILL", "AD": "AD"}


class _SignedKey(BaseModel):
    fieldKey: str
    sign: Literal[1, -1]


class _Pair(BaseModel):
    title: str
    itemKey: str
    baseKeys: list[_SignedKey]
    targetKeys: list[_SignedKey]


class _ConditionDiff(BaseModel):
    title: str
    itemKey: str
    baseText: str
    targetText: str
    differenceText: str
    baseFieldKey: str | None
    targetFieldKey: str | None


class _LLMNotice(BaseModel):
    severity: Literal["WARNING", "INFO"]
    title: str
    description: str
    possibleReasons: list[Literal["EXTRA_USAGE", "DISCOUNT_ENDED", "PRICE_CHANGED", "CONTRACT_VIOLATION", "ETC"]]


class _LLMCompare(BaseModel):
    comparable: bool
    notComparableReason: Literal["DIFFERENT_PROVIDER", "OUT_OF_PERIOD", "NO_COMMON_ITEMS", "NONE"]
    amountPairs: list[_Pair]
    conditionDiffs: list[_ConditionDiff]
    notices: list[_LLMNotice]


_INSTRUCTIONS = """두 서류를 비교한다. 기준 서류는 계약서, 비교할 서류는 고지서(BILL) 또는 광고(AD)다.
1) comparable: 같은 서비스·같은 상대방(회사명, 상품명, 고객번호)이고, 고지서 청구 기간이 계약 기간 안이면 true.
   아니면 false와 notComparableReason(DIFFERENT_PROVIDER / OUT_OF_PERIOD / NO_COMMON_ITEMS).
2) amountPairs: 서로 대응하는 금액 항목을 짝짓는다. 계산은 하지 않는다.
   한쪽이 여러 항목의 합이면 각 항목을 sign(1 더하기, -1 빼기)과 함께 넣는다.
   예) 계약서 monthlyFee ↔ 고지서 baseFee(+1), extraUsageFee(+1), discountAmount(-1)
   fieldKey는 반드시 각 서류의 [항목] 목록에 있는 key만 쓴다.
3) conditionDiffs: 금액이 아닌 조건 차이 (예: 광고 "평생 할인" vs 계약서 "12개월 할인"). differenceText는 "평생 → 12개월"처럼 짧게.
4) notices: 차이가 있으면 가능한 이유와 확인할 일. 쉬운 존댓말, title은 "확인 필요 · ..." 형식. 차이가 없으면 빈 배열."""


class ComparisonService:
    def __init__(self, be: BackendClient, llm: LLMClient):
        self.be = be
        self.llm = llm

    async def compare(self, req: ComparisonRequest) -> ComparisonResponse:
        if req.baseDocumentId == req.targetDocumentId:
            raise AppError(400, "INVALID_REQUEST", "같은 문서끼리는 비교할 수 없습니다.")
        base_doc, target_doc = await asyncio.gather(
            self.be.get_document(req.baseDocumentId, req.userId),
            self.be.get_document(req.targetDocumentId, req.userId),
        )
        require_analyzed(base_doc)
        require_analyzed(target_doc)

        if base_doc.get("docCategory") != "CONTRACT" or target_doc.get("docCategory") != TARGET_CATEGORY[req.compareType]:
            raise AppError(422, "NOT_COMPARABLE", "계약서와 고지서(또는 광고)를 골라 주세요.", reason="WRONG_DOCUMENT_TYPE")

        content = (
            f"[비교 종류] {req.compareType}\n"
            f"[기준 서류] {base_doc.get('title')}\n당사자: {base_doc.get('parties')}\n[항목]\n{fields_for_prompt(base_doc)}\n\n"
            f"[비교할 서류] {target_doc.get('title')}\n당사자: {target_doc.get('parties')}\n[항목]\n{fields_for_prompt(target_doc)}"
        )
        result = await self.llm.parse(_LLMCompare, instructions=_INSTRUCTIONS, content=content, max_output_tokens=2000)
        if not result.comparable:
            raise AppError(
                422,
                "NOT_COMPARABLE",
                "같은 서비스와 같은 기간의 서류를 골라 주세요.",
                reason=result.notComparableReason if result.notComparableReason != "NONE" else "NO_COMMON_ITEMS",
            )

        base_fields, target_fields = fields_by_key(base_doc), fields_by_key(target_doc)
        base_label = "계약서"
        target_label = self._target_label(req.compareType, target_fields)
        items: list[ComparisonItem] = []
        diffs: list[tuple[int, int, int, ComparisonItem]] = []  # (difference, base, target, item)

        for pair in result.amountPairs:
            base_amount = self._sum(pair.baseKeys, base_fields)
            target_amount = self._sum(pair.targetKeys, target_fields)
            if base_amount is None or target_amount is None:
                continue
            # 실제 − 약속 : BILL은 청구(비교 서류)가 실제, AD는 계약서가 실제
            difference = target_amount - base_amount if req.compareType == "BILL" else base_amount - target_amount
            item = ComparisonItem(
                title=pair.title,
                itemKey=pair.itemKey,
                base=CompareValue(label=base_label, value=format_won(base_amount), amount=base_amount),
                target=CompareValue(label=target_label, value=format_won(target_amount), amount=target_amount),
                difference=CompareValue(label="차이", value=format_won(difference, signed=True), amount=difference) if difference else None,
                evidence=self._evidence(req.baseDocumentId, base_fields, pair.baseKeys)
                + self._evidence(req.targetDocumentId, target_fields, pair.targetKeys),
            )
            items.append(item)
            diffs.append((difference, base_amount, target_amount, item))

        for cond in result.conditionDiffs:
            keys_base = [_SignedKey(fieldKey=cond.baseFieldKey, sign=1)] if cond.baseFieldKey in base_fields else []
            keys_target = [_SignedKey(fieldKey=cond.targetFieldKey, sign=1)] if cond.targetFieldKey in target_fields else []
            items.append(
                ComparisonItem(
                    title=cond.title,
                    itemKey=cond.itemKey,
                    base=CompareValue(label=base_label, value=cond.baseText),
                    target=CompareValue(label=target_label, value=cond.targetText),
                    difference=CompareValue(label="차이", value=cond.differenceText),
                    evidence=self._evidence(req.baseDocumentId, base_fields, keys_base)
                    + self._evidence(req.targetDocumentId, target_fields, keys_target),
                )
            )

        has_diff = any(d for d, *_ in diffs) or bool(result.conditionDiffs)
        status = "NEEDS_CHECK" if not items else "MISMATCH" if has_diff else "MATCH"
        return ComparisonResponse(
            comparisonId=req.comparisonId,
            baseDocumentId=req.baseDocumentId,
            targetDocumentId=req.targetDocumentId,
            compareType=req.compareType,
            status=status,  # type: ignore[arg-type]
            headline=HEADLINES[status],
            highlight=self._highlight(req.compareType, diffs, result.conditionDiffs),
            comparisons=items,
            notices=[Notice(**n.model_dump()) for n in result.notices] if status == "MISMATCH" else [],
            disclaimer=DISCLAIMER,
        )

    @staticmethod
    def _sum(keys: list[_SignedKey], fields: dict[str, dict]) -> int | None:
        total, used = 0, 0
        for k in keys:
            value = (fields.get(k.fieldKey) or {}).get("value")
            if isinstance(value, (int, float)) and fields[k.fieldKey].get("valueType") == "AMOUNT":
                total += int(value) * k.sign
                used += 1
        return total if used else None

    @staticmethod
    def _evidence(document_id: int, fields: dict[str, dict], keys: list[_SignedKey]) -> list[CompareEvidence]:
        out = []
        for k in keys:
            f = fields.get(k.fieldKey)
            if not f:
                continue
            loc = f.get("evidenceLocation") or {}
            out.append(CompareEvidence(documentId=document_id, text=f.get("evidence"), page=loc.get("page"), bbox=loc.get("bbox")))
        return out

    @staticmethod
    def _target_label(compare_type: str, fields: dict[str, dict]) -> str:
        if compare_type == "AD":
            return "광고"
        month = str((fields.get("billingMonth") or {}).get("value") or "")
        if len(month) >= 7 and month[4] == "-":
            return f"{int(month[5:7])}월 고지서"
        return "고지서"

    @staticmethod
    def _highlight(compare_type: str, diffs, condition_diffs) -> Highlight | None:
        positive = [d for d in diffs if d[0] != 0]
        if positive:
            difference, base, target, item = max(positive, key=lambda d: abs(d[0]))
            if compare_type == "BILL":
                label = "이번 달 초과 추정액" if difference > 0 else "이번 달 덜 청구된 금액"
                formula = f"청구액 {format_won(target)} - 약정액 {format_won(base)}"
            else:
                label = "가장 큰 조건 차이"
                formula = f"계약서 {format_won(base)} - 광고 {format_won(target)}"
            return Highlight(label=label, amount=abs(difference), value=format_won(abs(difference)), formula=formula)
        if condition_diffs:
            c = condition_diffs[0]
            return Highlight(label="가장 큰 조건 차이", amount=None, value=c.differenceText, formula=None)
        return None


def describe_items(comparison: dict) -> str:
    """확인 요청 문자용: 비교 결과를 사실 목록으로."""
    lines = []
    for c in comparison.get("comparisons") or []:
        diff = (c.get("difference") or {}).get("value")
        if not diff:
            continue
        base, target = c.get("base") or {}, c.get("target") or {}
        lines.append(f"- {c.get('title')}: {base.get('label')} {base.get('value')} / {target.get('label')} {target.get('value')} / 차이 {diff}")
    return "\n".join(lines)

