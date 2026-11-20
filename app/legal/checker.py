"""법적 위험 점검 = 규칙 엔진 + 법령 RAG + LLM 판단."""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.clients.llm import LLMClient
from app.legal.repository import DOC_TYPE_TAGS, LawArticle, LawDbUnavailable, LawRepository, law_url
from app.legal.rules import RuleContext, evaluate
from app.ocr.models import OcrDocument
from app.schemas.analysis import ClauseEvidence, Field, HelpContact, LawRef, LegalCheck, LegalIssue
from app.services.analysis.extract import locate

logger = logging.getLogger("chagok.legal")

DISCLAIMER = "이 내용은 법률 자문이 아닌 참고 정보예요. 정확한 판단은 전문가와 상담하세요."

HELP_CONTACTS: dict[str, list[HelpContact]] = {
    "EMPLOYMENT_CONTRACT": [HelpContact(name="고용노동부 고객상담센터", phone="1350")],
    "SERVICE_CONTRACT": [HelpContact(name="1372 소비자상담센터", phone="1372")],
    "BILL": [HelpContact(name="1372 소비자상담센터", phone="1372")],
    "ADVERTISEMENT": [HelpContact(name="1372 소비자상담센터", phone="1372")],
}
LEGAL_AID = HelpContact(name="대한법률구조공단", phone="132")
SEVERITY_ORDER = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


class _LLMIssue(BaseModel):
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    title: str
    explanation: str
    fieldKey: str | None
    clauseText: str
    clausePage: int | None
    lawIds: list[int]
    recommendedAction: str
    needsExpert: bool


class _LLMLegal(BaseModel):
    issues: list[_LLMIssue]


_INSTRUCTIONS = """너는 한국 법령을 근거로 서류의 위험 조항을 찾는 점검기다. 사용자는 법을 잘 모르는 일반인이다.
- 주어진 [법령 조문] 목록 안의 조문만 근거로 쓴다. 근거 조문이 없으면 이슈를 만들지 않는다. lawIds에 조문 번호(#)를 넣는다.
- [규칙 엔진 결과]에 이미 있는 내용은 다시 만들지 않는다.
- 사용자 조건(나이, 국적, 지역, 주거 형태, 복지 유형)과 관련 있는 위험을 우선한다.
- clauseText: 문제가 되는 원문 문장을 그대로 복사 (80자 이내)
- explanation, recommendedAction: 쉬운 말 1~2문장, 존댓말(~요)
- 위험이 없으면 issues를 빈 배열로 준다. 최대 5개."""


@dataclass
class LegalInput:
    doc_type: str
    doc_category: str
    document: OcrDocument
    fields: list[Field]
    user: dict | None
    base_date: date


def _age(user: dict | None, base_date: date) -> int | None:
    from app.utils.text import calc_age

    return calc_age((user or {}).get("birthDate"), base_date)


def _llm_allowed(user: dict | None) -> bool:
    consents = (user or {}).get("consents") or {}
    return bool(consents.get("overseasTransfer"))


def _profile_text(user: dict, age: int | None) -> str:
    """외부 LLM에는 생년월일 대신 계산된 나이만 보냅니다."""
    p = user.get("profile") or {}
    region = p.get("region") or {}
    return (
        f"나이: {f'만 {age}세' if age is not None else '알 수 없음'}, 국적: {p.get('nationality')}, "
        f"지역: {region.get('sido', '')} {region.get('sigungu', '')}, 주거: {p.get('housingType')}, 복지: {p.get('welfareType')}"
    )


async def _law_ref(repo: LawRepository, law_name: str, article: str) -> LawRef:
    found = await repo.get_article(law_name, article) if repo.enabled else None
    return LawRef(
        lawName=law_name,
        article=article,
        excerpt=(found.content[:200] if found else None),
        url=found.url if found else law_url(law_name, article),
    )


async def run_legal_check(data: LegalInput, llm: LLMClient, repo: LawRepository) -> tuple[LegalCheck | None, list[str]]:
    """반환: (legalCheck 또는 None, warnings)"""
    warnings: list[str] = []
    user = data.user
    if not user or user.get("consentRequired") or not user.get("profile"):
        return None, warnings

    age = _age(user, data.base_date)
    fields_by_key = {f.key: f for f in data.fields}
    ctx = RuleContext(
        doc_type=data.doc_type,
        doc_category=data.doc_category,
        fields=fields_by_key,
        age=age,
        profile=user.get("profile"),
        consents=user.get("consents") or {},
        base_date=data.base_date,
    )
    rule_result = evaluate(ctx)
    skipped = list(rule_result.skipped)

    issues: list[LegalIssue] = []
    for r in rule_result.issues:
        f = fields_by_key.get(r.field_key) if r.field_key else None
        issues.append(
            LegalIssue(
                severity=r.severity,  # type: ignore[arg-type]
                title=r.title,
                explanation=r.explanation,
                source="RULE",
                fieldKey=r.field_key,
                matchedCondition=r.matched,
                clauseEvidence=(
                    ClauseEvidence(
                        text=f.evidence,
                        page=f.evidenceLocation.page if f.evidenceLocation else None,
                        bbox=f.evidenceLocation.bbox if f.evidenceLocation else None,
                    )
                    if f and f.evidence
                    else None
                ),
                laws=[await _law_ref(repo, r.law_name, r.article)],
                recommendedAction=r.action,
                needsExpert=r.needs_expert,
            )
        )

    if data.doc_category in ("CONTRACT", "BILL") and llm.available:
        if not _llm_allowed(user):
            skipped.append("LLM_REVIEW_NO_OVERSEAS_CONSENT")
        else:
            try:
                issues += await _llm_issues(data, ctx, issues, age, llm, repo)
            except LawDbUnavailable:
                warnings.append("LAW_DB_UNAVAILABLE")
            except Exception as exc:  # noqa: BLE001  법적 점검 실패해도 분석은 정상 응답
                logger.warning("legal LLM check failed: %s", exc)
                warnings.append("LEGAL_CHECK_PARTIAL")

    issues.sort(key=lambda i: SEVERITY_ORDER[i.severity], reverse=True)
    risk = max((i.severity for i in issues), key=lambda s: SEVERITY_ORDER[s], default="NONE")
    version = await repo.latest_sync_date()
    return (
        LegalCheck(
            riskLevel=risk,  # type: ignore[arg-type]
            issues=issues,
            skippedRules=skipped,
            helpContacts=[LEGAL_AID, *HELP_CONTACTS.get(data.doc_type, [])],
            lawVersionDate=version.isoformat() if version else None,
            disclaimer=DISCLAIMER,
        ),
        warnings,
    )


async def _llm_issues(data: LegalInput, ctx: RuleContext, rule_issues: list[LegalIssue], age: int | None, llm: LLMClient, repo: LawRepository) -> list[LegalIssue]:
    if not repo.enabled:
        raise LawDbUnavailable()
    clauses = "\n".join(f"- {f.label}: {f.evidence}" for f in data.fields if f.evidence)
    query = f"{data.doc_type} {clauses[:1500]}"
    articles: list[LawArticle] = await repo.search(query, DOC_TYPE_TAGS.get(data.doc_type, ["civil"]), k=6)
    if not articles:
        return []

    law_list = "\n".join(f"#{i} {a.law_name} {a.article} {a.title or ''}: {a.content[:400]}" for i, a in enumerate(articles))
    rule_list = "\n".join(f"- {i.title}" for i in rule_issues) or "없음"
    content = (
        f"[서류 종류] {data.doc_type}\n[사용자 조건] {_profile_text(data.user or {}, age)}\n"
        f"[규칙 엔진 결과]\n{rule_list}\n[법령 조문]\n{law_list}\n\n[서류 원문]\n{data.document.full_text[:16000]}"
    )
    result = await llm.parse(_LLMLegal, instructions=_INSTRUCTIONS, content=content, max_output_tokens=2500)

    issues: list[LegalIssue] = []
    for item in result.issues[:5]:
        laws = [articles[i] for i in item.lawIds if 0 <= i < len(articles)]
        if not laws:  # 조문 인용 없는 이슈는 버림
            continue
        location = locate(data.document, item.clausePage, item.clauseText)
        issues.append(
            LegalIssue(
                severity=item.severity,
                title=item.title,
                explanation=item.explanation,
                source="LLM",
                fieldKey=item.fieldKey if item.fieldKey in ctx.fields else None,
                matchedCondition={},
                clauseEvidence=ClauseEvidence(
                    text=item.clauseText,
                    page=location.page if location else item.clausePage,
                    bbox=location.bbox if location else None,
                ),
                laws=[LawRef(lawName=a.law_name, article=a.article, excerpt=a.content[:200], url=a.url) for a in laws],
                recommendedAction=item.recommendedAction,
                needsExpert=item.needsExpert,
            )
        )
    return issues
