"""항목명 정규화 + 조건 추출 (LLM) → 코드 후처리(값 변환, 근거 검증, 위치, needsReview)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.clients.llm import LLMClient
from app.ocr.models import OcrDocument
from app.schemas.analysis import CalendarEvent, Conditions, EvidenceLocation, Field, Party, PaymentSchedule
from app.utils.text import best_window_similarity, contains_loose, parse_amount, parse_iso_date, similarity

# 문서 종류별 표준 키 (항목명 정규화 기준)
STANDARD_KEYS: dict[str, str] = {
    "LEASE_CONTRACT": "propertyAddress(소재지), deposit(보증금), monthlyRent(월세·차임), maintenanceFee(관리비), contractStartDate, contractEndDate, "
    "rentPaymentDay(월세 납부일, NUMBER), earlyTerminationPenalty(중도 해지 위약금, PENALTY), specialTerms(특약, TEXT)",
    "EMPLOYMENT_CONTRACT": "workplace(근무처), contractStartDate, contractEndDate, hourlyWage(시급), monthlySalary(월급), payDay(급여일, NUMBER), "
    "workHoursPerDay(1일 근로시간, NUMBER), workHoursPerWeek(1주 근로시간, NUMBER), workDays(근무요일, TEXT), weeklyHolidayPay(주휴수당, TEXT)",
    "SERVICE_CONTRACT": "provider(계약 상대), serviceName(서비스·상품명), monthlyFee(월 이용료), totalAmount(총 금액), contractStartDate, contractEndDate, "
    "commitmentMonths(약정 개월, NUMBER), earlyTerminationPenalty(위약금, PENALTY), interestRate(이자율, NUMBER, 연 % 숫자), cancellationTerms(해지 조건, TEXT)",
    "BILL": "issuer(청구처), customerNumber(고객번호), billingMonth(청구 월, TEXT 예: 2026-11), billAmount(청구금액), baseFee(기본료), extraUsageFee(부가사용료), "
    "discountAmount(할인액, 양수), paymentDueDate(납부기한), paymentMethod(납부방법, TEXT), lateFee(연체료)",
    "ADVERTISEMENT": "advertiser(광고주), productName(상품명), promotedPrice(광고 가격), promotionPeriod(혜택 기간, TEXT), conditions(조건, TEXT)",
    "NOTICE": "target(대상), eventDate(일시), location(장소), preparation(준비물, TEXT), applyDeadline(신청 기한)",
    "ETC": "자유롭게 camelCase 키를 만든다",
}


class _LLMField(BaseModel):
    key: str
    label: str
    originalLabel: str
    valueType: Literal["AMOUNT", "DATE", "PENALTY", "TEXT", "NUMBER"]
    value: str
    penaltyRate: float | None
    penaltyAmount: int | None
    evidence: str
    page: int
    confidence: float
    inferred: bool


class _LLMParty(BaseModel):
    role: str
    name: str


class _LLMPayment(BaseModel):
    fieldKey: str
    label: str
    recurrence: Literal["MONTHLY", "YEARLY", "ONCE"]
    dayOfMonth: int | None
    startDate: str | None
    endDate: str | None


class _LLMEvent(BaseModel):
    type: Literal["PAYMENT", "EXPIRY", "ACTION"]
    fieldKey: str
    title: str
    date: str
    remindBeforeDays: list[int]


class _LLMExtraction(BaseModel):
    title: str
    summary: str
    parties: list[_LLMParty]
    fields: list[_LLMField]
    paymentSchedules: list[_LLMPayment]
    calendarEvents: list[_LLMEvent]


_INSTRUCTIONS = """너는 한국어 서류(계약서·고지서·안내문·광고)에서 정보를 뽑는 추출기다. 반드시 원문에 있는 내용만 쓴다.

[fields] 서류의 모든 핵심 값을 한 줄씩 넣는다 (금액·날짜·위약금·납부일·조건).
- key: 아래 표준 키를 우선 사용하고, 없으면 camelCase로 만든다. label: 표준 한국어 이름, originalLabel: 원문에 적힌 항목명
- valueType: AMOUNT(원 단위 정수), DATE(YYYY-MM-DD), PENALTY(위약금), TEXT, NUMBER
- value: AMOUNT는 숫자만(예: 10000000), DATE는 YYYY-MM-DD, PENALTY는 원문 요약 문장, 그 외는 짧은 값
- PENALTY면 penaltyRate(비율 0~1) 또는 penaltyAmount(원)를 채우고, 아니면 둘 다 null
- evidence: 그 값이 적힌 원문 문장을 그대로 복사 (60자 이내), page: [N페이지] 표시의 N
- confidence: 0~1. 흐리거나 애매하면 낮게 준다
- 날짜에 연도가 없어 기준일로 추정했으면 inferred: true

[parties] 당사자 role(LESSOR, LESSEE, EMPLOYER, EMPLOYEE, PROVIDER, CUSTOMER, ISSUER, ADVERTISER 등)과 이름

[paymentSchedules] 반복해서 내는 돈. fieldKey는 금액 field의 key

[calendarEvents] 캘린더에 넣을 일정. 하나의 일정은 하나만 넣는다 (중복 금지).
- type: PAYMENT(납부, 반복이면 첫 납부일), EXPIRY(만료·종료·기한), ACTION(그 전에 해야 할 행동: 갱신 통보, 해지 통보, 이의신청, 신청 마감)
- 계약서에 "만기 N개월 전까지 통지" 같은 조항이 있으면 ACTION 일정을 계산해서 넣는다
- fieldKey: 근거가 된 field의 key (ACTION은 기준이 된 날짜의 key)
- remindBeforeDays: PAYMENT [3,1], EXPIRY [60,30,7], ACTION [14,7,1] 를 기본으로 한다

[title] 서류 제목, [summary] 3줄 이내 한국어 요약 (금액·기간 포함)"""


async def extract(document: OcrDocument, doc_type: str, base_date: date, llm: LLMClient) -> _LLMExtraction:
    content = (
        f"[기준일] {base_date.isoformat()}\n[서류 종류] {doc_type}\n[표준 키] {STANDARD_KEYS.get(doc_type, STANDARD_KEYS['ETC'])}\n\n"
        f"[OCR 원문]\n{document.full_text[:24000]}"
    )
    return await llm.parse(_LLMExtraction, instructions=_INSTRUCTIONS, content=content, max_output_tokens=4000)


# ---------- 후처리 ----------

def _convert_value(f: _LLMField):
    if f.valueType == "AMOUNT":
        return parse_amount(f.value)
    if f.valueType == "DATE":
        return parse_iso_date(f.value)
    if f.valueType == "PENALTY":
        rate = f.penaltyRate
        if rate is not None and rate > 1:
            rate = rate / 100
        return {"description": f.value, "amount": f.penaltyAmount, "rate": rate}
    if f.valueType == "NUMBER":
        try:
            number = float(str(f.value).replace(",", "").replace("%", "").strip())
            return int(number) if number.is_integer() else number
        except ValueError:
            return None
    return f.value.strip() or None


def locate(document: OcrDocument, page: int | None, text: str | None) -> EvidenceLocation | None:
    """근거 문장이 있는 페이지와 bbox를 찾습니다."""
    if not text:
        return None
    pages = [document.page(page)] if page and document.page(page) else []
    if not pages or not contains_loose(pages[0].text, text):
        found = next((p for p in document.pages if contains_loose(p.text, text)), None)
        pages = [found] if found else pages
    if not pages or pages[0] is None:
        return None
    target = pages[0]
    best, best_score = None, 0.0
    for block in target.blocks:
        inside = contains_loose(block.text, text) or (len(block.text) > 4 and contains_loose(text, block.text))
        score = 1.0 if inside else similarity(block.text, text)
        if score > best_score:
            best, best_score = block, score
    bbox = best.bbox if best is not None and best_score >= 0.5 else None
    return EvidenceLocation(page=target.page, bbox=bbox)


def build_fields(raw: _LLMExtraction, document: OcrDocument, review_confidence: float) -> list[Field]:
    fields: list[Field] = []
    seen: set[str] = set()
    full_text = document.full_text
    for f in raw.fields:
        if f.key in seen:
            continue
        seen.add(f.key)
        value = _convert_value(f)
        confidence = max(0.0, min(1.0, f.confidence))
        evidence = f.evidence.strip() or None
        # 근거가 원문에 없으면 지어낸 값일 수 있으니 신뢰도를 낮춥니다
        if evidence and best_window_similarity(full_text, evidence) < 0.8:
            confidence = min(confidence, 0.5)
        if value is None:
            confidence = min(confidence, 0.5)
        fields.append(
            Field(
                key=f.key,
                label=f.label,
                originalLabel=f.originalLabel or f.label,
                value=value,
                valueType=f.valueType,
                evidence=evidence,
                evidenceLocation=locate(document, f.page, evidence),
                confidence=round(confidence, 2),
                inferred=f.inferred,
                needsReview=confidence < review_confidence or f.inferred or value is None,
            )
        )
    return fields


def build_parties(raw: _LLMExtraction) -> list[Party]:
    return [Party(role=p.role.upper(), name=p.name) for p in raw.parties if p.name.strip()]


def build_conditions(raw: _LLMExtraction, field_keys: set[str]) -> Conditions:
    schedules = []
    for s in raw.paymentSchedules:
        if s.fieldKey not in field_keys:
            continue
        schedules.append(
            PaymentSchedule(
                fieldKey=s.fieldKey,
                label=s.label,
                recurrence=s.recurrence,
                dayOfMonth=s.dayOfMonth if s.dayOfMonth and 1 <= s.dayOfMonth <= 31 else None,
                startDate=parse_iso_date(s.startDate),
                endDate=parse_iso_date(s.endDate),
            )
        )
    return Conditions(paymentSchedules=schedules)


def _rrule(schedule: PaymentSchedule | None) -> str | None:
    if schedule is None or schedule.recurrence == "ONCE":
        return None
    until = f";UNTIL={schedule.endDate.replace('-', '')}" if schedule.endDate else ""
    if schedule.recurrence == "MONTHLY" and schedule.dayOfMonth:
        return f"FREQ=MONTHLY;BYMONTHDAY={schedule.dayOfMonth}{until}"
    if schedule.recurrence == "YEARLY":
        return f"FREQ=YEARLY{until}"
    return None


def build_events(raw: _LLMExtraction, field_keys: set[str], conditions: Conditions) -> list[CalendarEvent]:
    schedules = {s.fieldKey: s for s in conditions.paymentSchedules}
    events: list[CalendarEvent] = []
    seen: set[tuple[str, str, str | None]] = set()
    for e in raw.calendarEvents:
        event_date = parse_iso_date(e.date)
        if not event_date:
            continue
        field_key = e.fieldKey if e.fieldKey in field_keys else None
        key = (e.type, event_date, field_key)
        if key in seen:
            continue
        seen.add(key)
        defaults = {"PAYMENT": [3, 1], "EXPIRY": [60, 30, 7], "ACTION": [14, 7, 1]}[e.type]
        remind = sorted({d for d in e.remindBeforeDays if 0 < d <= 365}, reverse=True) or defaults
        events.append(
            CalendarEvent(
                type=e.type,
                fieldKey=field_key,
                title=e.title,
                date=event_date,
                rrule=_rrule(schedules.get(field_key)) if e.type == "PAYMENT" and field_key else None,
                remindBeforeDays=remind,
            )
        )
    return sorted(events, key=lambda ev: ev.date)
