"""문서 종류 분류: 키워드 규칙 → 애매하면 LLM (이미지 모델 없음)."""

from dataclasses import dataclass

from pydantic import BaseModel

from app.clients.llm import LLMClient
from app.schemas.analysis import DocCategory, DocType

CATEGORY: dict[str, str] = {
    "LEASE_CONTRACT": "CONTRACT",
    "EMPLOYMENT_CONTRACT": "CONTRACT",
    "SERVICE_CONTRACT": "CONTRACT",
    "BILL": "BILL",
    "ADVERTISEMENT": "AD",
    "NOTICE": "INFO",
    "ETC": "INFO",
}

KEYWORDS: dict[str, list[tuple[str, str]]] = {
    "LEASE_CONTRACT": [("임대인", "임대인·임차인 표기"), ("임차인", "임대인·임차인 표기"), ("보증금", "보증금 조항"), ("차임", "차임(월세) 조항"),
                       ("임대차", "임대차 문구"), ("전입", "전입 관련 문구"), ("확정일자", "확정일자 문구")],
    "EMPLOYMENT_CONTRACT": [("근로계약", "근로계약 문구"), ("근로자", "근로자 표기"), ("사용자", "사용자 표기"), ("임금", "임금 조항"),
                            ("근로시간", "근로시간 조항"), ("시급", "시급 표기"), ("주휴", "주휴수당 문구")],
    "SERVICE_CONTRACT": [("이용약관", "이용약관 문구"), ("약정", "약정 조건"), ("가입", "가입 문구"), ("해지", "해지 조건"),
                         ("서비스", "서비스 문구"), ("용역", "용역 문구"), ("위약금", "위약금 조항")],
    "BILL": [("고지서", "고지서 표기"), ("청구서", "청구서 표기"), ("납부기한", "납부기한 표기"), ("청구금액", "청구금액 표기"),
             ("납기", "납기 표기"), ("미납", "미납 문구"), ("고객번호", "고객번호 표기")],
    "NOTICE": [("안내", "안내 문구"), ("공지", "공지 문구"), ("알려드립니다", "안내 문구"), ("일시", "일시 항목"), ("장소", "장소 항목")],
    "ADVERTISEMENT": [("광고", "광고 표기"), ("이벤트", "이벤트 문구"), ("혜택", "혜택 문구"), ("무료", "무료 문구"), ("할인", "할인 홍보"),
                      ("수신거부", "수신거부 안내"), ("지금 바로", "가입 유도 문구"), ("평생", "과장 표현")],
}

# 서명란이 있으면 계약서 쪽으로 가중치
CONTRACT_SIGNS = [("서명", "서명란 존재"), ("(인)", "날인란 존재"), ("계약기간", "계약기간 조항 존재")]


@dataclass
class Classification:
    docType: DocType
    docCategory: DocCategory
    confidence: float
    reasons: list[str]


def score_keywords(text: str, hint: str | None) -> dict[str, tuple[int, list[str]]]:
    scores: dict[str, tuple[int, list[str]]] = {}
    for doc_type, words in KEYWORDS.items():
        points, reasons = 0, []
        for word, reason in words:
            if word in text:
                points += 1
                if reason not in reasons:
                    reasons.append(reason)
        if CATEGORY[doc_type] == "CONTRACT":
            for word, reason in CONTRACT_SIGNS:
                if word in text:
                    points += 1
                    if reason not in reasons:
                        reasons.append(reason)
        if hint == doc_type:
            points += 2
        scores[doc_type] = (points, reasons)
    return scores


class _LLMClassification(BaseModel):
    docType: DocType
    docCategory: DocCategory
    confidence: float
    reasons: list[str]


_INSTRUCTIONS = """너는 한국어 서류 분류기다. 주어진 OCR 텍스트가 어떤 서류인지 고른다.
- docType: LEASE_CONTRACT(임대차계약서), EMPLOYMENT_CONTRACT(근로계약서), SERVICE_CONTRACT(용역·서비스·통신 계약), BILL(고지서·청구서), NOTICE(공지·안내문), ADVERTISEMENT(광고·홍보물), ETC
- docCategory: CONTRACT / BILL / AD / INFO
- 고지서처럼 꾸민 광고(납부 안내 형식의 홍보 우편, 가입 유도, 혜택 강조, 수신거부 문구)는 ADVERTISEMENT, AD로 분류한다.
- reasons: 판별 근거를 짧은 한국어 문장으로 2~3개 (예: "양 당사자 서명란 존재")
- confidence: 0~1"""


async def classify(text: str, hint: str | None, llm: LLMClient) -> Classification:
    scores = score_keywords(text, hint)
    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    (top_type, (top_score, top_reasons)), (_, (second_score, _)) = ranked[0], ranked[1]

    # 광고 신호가 강하면 고지서·계약서보다 우선 (고지서처럼 꾸민 광고 구분)
    ad_score = scores["ADVERTISEMENT"][0]
    clear = top_score >= 3 and top_score - second_score >= 2 and not (ad_score >= 3 and top_type != "ADVERTISEMENT")

    if clear or not llm.available:
        if top_score == 0:
            return Classification("ETC", "INFO", 0.3, ["서류 종류를 판별할 단서가 부족함"])
        confidence = min(0.98, 0.6 + 0.08 * (top_score - second_score))
        return Classification(top_type, CATEGORY[top_type], round(confidence, 2), top_reasons[:3])  # type: ignore[arg-type]

    result = await llm.parse(
        _LLMClassification,
        instructions=_INSTRUCTIONS,
        content=f"[사용자가 고른 종류] {hint or '없음'}\n[OCR 텍스트 앞부분]\n{text[:4000]}",
        light=True,
        max_output_tokens=300,
    )
    category = CATEGORY[result.docType]
    return Classification(result.docType, category, round(max(0.0, min(1.0, result.confidence)), 2), result.reasons[:3])  # type: ignore[arg-type]
