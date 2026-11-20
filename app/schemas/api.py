"""요약 카드 · 채팅 · 질문 칩 · 서류 비교 · 확인 요청 문자 요청/응답."""

from typing import Literal

from pydantic import BaseModel, Field


# ---------- 쉬운말 요약 카드 ----------

class SummaryCardRequest(BaseModel):
    documentId: int
    userId: int


class SummaryItem(BaseModel):
    label: str | None = None
    value: str | None = None
    fieldKey: str | None = None
    text: str | None = None
    checkKey: str | None = None
    done: bool | None = None


class SummarySection(BaseModel):
    type: Literal["KEY_VALUE", "BULLET", "CHECKLIST"]
    title: str
    items: list[SummaryItem]


class SummaryCardResponse(BaseModel):
    documentId: int
    category: str
    title: str
    oneLineSummary: str
    sections: list[SummarySection]


# ---------- 서류 기반 AI 채팅 ----------

class ChatRequest(BaseModel):
    userId: int
    documentId: int
    question: str = Field(min_length=1, max_length=500)


class Citation(BaseModel):
    sourceType: Literal["DOCUMENT", "LAW"]
    documentId: int | None = None
    page: int | None = None
    bbox: list[float] | None = None
    snippet: str | None = None
    score: float | None = None
    lawName: str | None = None
    article: str | None = None
    url: str | None = None


class ChatResponse(BaseModel):
    answer: str
    answerType: Literal["ANSWERED", "NOT_IN_DOCUMENT", "NEEDS_EXPERT"]
    citations: list[Citation]
    disclaimer: str | None
    followUpQuestions: list[str]


# ---------- 자주 묻는 질문 칩 ----------

class SuggestionsRequest(BaseModel):
    chatId: int
    userId: int
    documentId: int
    count: int = Field(default=2, ge=1, le=6)


class Suggestion(BaseModel):
    text: str
    category: Literal["AMOUNT", "DEADLINE", "PENALTY", "PAYMENT", "GENERAL"]


class SuggestionsResponse(BaseModel):
    chatId: int
    documentId: int
    suggestions: list[Suggestion]


# ---------- 서류 비교 ----------

class ComparisonRequest(BaseModel):
    comparisonId: int
    userId: int
    baseDocumentId: int
    targetDocumentId: int
    compareType: Literal["BILL", "AD"]


class CompareValue(BaseModel):
    label: str
    value: str
    amount: int | None = None


class CompareEvidence(BaseModel):
    documentId: int
    text: str | None
    page: int | None
    bbox: list[float] | None


class ComparisonItem(BaseModel):
    title: str
    itemKey: str
    base: CompareValue
    target: CompareValue
    difference: CompareValue | None
    evidence: list[CompareEvidence]


class Highlight(BaseModel):
    label: str
    amount: int | None
    value: str
    formula: str | None


class Notice(BaseModel):
    severity: Literal["WARNING", "INFO"]
    title: str
    description: str
    possibleReasons: list[str]


class ComparisonResponse(BaseModel):
    comparisonId: int
    baseDocumentId: int
    targetDocumentId: int
    compareType: Literal["BILL", "AD"]
    status: Literal["MISMATCH", "MATCH", "NEEDS_CHECK"]
    headline: str
    highlight: Highlight | None
    comparisons: list[ComparisonItem]
    notices: list[Notice]
    disclaimer: str


# ---------- 확인 요청 문자 ----------

class InquiryRequest(BaseModel):
    userId: int
    tone: Literal["POLITE", "FRIENDLY", "FORMAL"] = "POLITE"
    extraRequest: str | None = Field(default=None, max_length=200)


class InquiryMessage(BaseModel):
    text: str
    type: Literal["SMS", "LMS"]
    byteLength: int


class InquiryResponse(BaseModel):
    comparisonId: int
    message: InquiryMessage
    tone: Literal["POLITE", "FRIENDLY", "FORMAL"]
