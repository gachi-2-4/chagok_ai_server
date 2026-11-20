"""문서 분석 API 응답 (POST /ai/documents/{id}/analysis)."""

from typing import Any, Literal

from pydantic import BaseModel

DocType = Literal["LEASE_CONTRACT", "EMPLOYMENT_CONTRACT", "SERVICE_CONTRACT", "BILL", "NOTICE", "ADVERTISEMENT", "ETC"]
DocCategory = Literal["CONTRACT", "BILL", "AD", "INFO"]
ValueType = Literal["AMOUNT", "DATE", "PENALTY", "TEXT", "NUMBER"]
EventType = Literal["PAYMENT", "EXPIRY", "ACTION"]
Level = Literal["HIGH", "MEDIUM", "LOW"]


class EvidenceLocation(BaseModel):
    page: int
    bbox: list[float] | None = None


class Party(BaseModel):
    role: str
    name: str


class Field(BaseModel):
    key: str
    label: str
    originalLabel: str
    value: Any
    valueType: ValueType
    evidence: str | None
    evidenceLocation: EvidenceLocation | None
    confidence: float
    inferred: bool
    needsReview: bool


class PaymentSchedule(BaseModel):
    fieldKey: str
    label: str
    recurrence: Literal["MONTHLY", "YEARLY", "ONCE"]
    dayOfMonth: int | None
    startDate: str | None
    endDate: str | None


class Conditions(BaseModel):
    paymentSchedules: list[PaymentSchedule]


class CalendarEvent(BaseModel):
    type: EventType
    fieldKey: str | None
    title: str
    date: str
    rrule: str | None
    remindBeforeDays: list[int]


class UserRelevance(BaseModel):
    level: Literal["RELEVANT", "PARTIAL", "NOT_RELEVANT"]
    reasons: list[str]


class LawRef(BaseModel):
    lawName: str
    article: str
    excerpt: str | None
    url: str


class ClauseEvidence(BaseModel):
    text: str
    page: int | None
    bbox: list[float] | None


class LegalIssue(BaseModel):
    severity: Level
    title: str
    explanation: str
    source: Literal["RULE", "LLM"]
    fieldKey: str | None
    matchedCondition: dict[str, Any]
    clauseEvidence: ClauseEvidence | None
    laws: list[LawRef]
    recommendedAction: str
    needsExpert: bool


class HelpContact(BaseModel):
    name: str
    phone: str


class LegalCheck(BaseModel):
    riskLevel: Literal["HIGH", "MEDIUM", "LOW", "NONE"]
    issues: list[LegalIssue]
    skippedRules: list[str]
    helpContacts: list[HelpContact]
    lawVersionDate: str | None
    disclaimer: str


class KeyCost(BaseModel):
    fieldKey: str
    label: str
    amount: int


class KeyDeadline(BaseModel):
    fieldKey: str | None
    label: str
    date: str
    actionLabel: str | None
    actionDate: str | None


class Priority(BaseModel):
    level: Level
    score: int
    reasons: list[str]
    keyCost: KeyCost | None
    keyDeadline: KeyDeadline | None


class AnalysisResponse(BaseModel):
    documentId: int
    docType: DocType
    docTypeConfidence: float
    docCategory: DocCategory
    classificationReasons: list[str]
    title: str
    summary: str
    parties: list[Party]
    fields: list[Field]
    conditions: Conditions
    calendarEvents: list[CalendarEvent]
    needsReview: bool
    userRelevance: UserRelevance | None
    fullText: str
    pageCount: int
    legalCheck: LegalCheck | None
    priority: Priority
    warnings: list[str]
