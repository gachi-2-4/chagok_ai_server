"""사용자 관련도(userRelevance): 온보딩 프로필과 서류 내용(수신인·지역·대상 조건) 비교."""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.clients.llm import LLMClient
from app.schemas.analysis import Party, UserRelevance
from app.utils.text import calc_age


class _LLMRelevance(BaseModel):
    level: Literal["RELEVANT", "PARTIAL", "NOT_RELEVANT"]
    reasons: list[str]


_INSTRUCTIONS = """서류가 이 사용자에게 해당하는지 판단한다.
- RELEVANT: 사용자가 당사자이거나 사용자 조건(지역·주거·나이·복지)에 맞는 서류
- PARTIAL: 일부만 해당하거나 확인이 필요
- NOT_RELEVANT: 다른 지역·대상 안내문처럼 사용자와 관계없는 서류
- reasons: 쉬운 존댓말(~요) 1~3개"""


async def judge_relevance(
    user: dict | None,
    doc_type: str,
    title: str,
    summary: str,
    parties: list[Party],
    base_date: date,
    llm: LLMClient,
) -> UserRelevance | None:
    if not user or not user.get("profile") or user.get("consentRequired"):
        return None
    if not (user.get("consents") or {}).get("overseasTransfer") or not llm.available:
        return None  # 해외 이전 동의가 없으면 프로필을 외부 LLM에 보내지 않음
    p = user["profile"]
    region = p.get("region") or {}
    age = calc_age(user.get("birthDate"), base_date)
    content = (
        f"[사용자] 나이: {age if age is not None else '알 수 없음'}, 국적: {p.get('nationality')}, 지역: {region.get('sido', '')} {region.get('sigungu', '')}, "
        f"주거: {p.get('housingType')}, 복지: {p.get('welfareType')}\n"
        f"[서류] 종류: {doc_type}, 제목: {title}\n요약: {summary}\n당사자: {', '.join(f'{x.role}' for x in parties) or '없음'}"
    )
    result = await llm.parse(_LLMRelevance, instructions=_INSTRUCTIONS, content=content, light=True, max_output_tokens=300)
    return UserRelevance(level=result.level, reasons=result.reasons[:3])
