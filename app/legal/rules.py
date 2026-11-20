"""규칙 엔진: 나이·금액·시간처럼 기준이 숫자인 점검은 LLM이 아니라 코드로 판정합니다."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.legal.thresholds import ThresholdTable, region_group
from app.schemas.analysis import Field
from app.utils.text import format_won


@dataclass
class RuleIssue:
    code: str
    severity: str
    title: str
    explanation: str
    law_name: str
    article: str
    field_key: str | None
    matched: dict[str, Any]
    action: str
    needs_expert: bool = False


@dataclass
class RuleContext:
    doc_type: str
    doc_category: str
    fields: dict[str, Field]
    age: int | None
    profile: dict | None
    consents: dict
    base_date: date
    thresholds: ThresholdTable = field(default_factory=ThresholdTable)

    def amount(self, key: str) -> int | None:
        f = self.fields.get(key)
        return f.value if f is not None and isinstance(f.value, int) else None

    def number(self, key: str) -> float | None:
        f = self.fields.get(key)
        return float(f.value) if f is not None and isinstance(f.value, (int, float)) else None


@dataclass
class RuleResult:
    issues: list[RuleIssue] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _minor_contract(ctx: RuleContext, out: RuleResult) -> None:
    if ctx.doc_category != "CONTRACT":
        return
    if ctx.age is None:
        out.skipped.append("MINOR_CONTRACT")
        return
    limit = ctx.thresholds.get("MINOR_AGE", ctx.base_date)
    if limit and ctx.age < limit.value and not ctx.consents.get("guardianConsent"):
        out.issues.append(
            RuleIssue(
                code="MINOR_CONTRACT",
                severity="HIGH",
                title="미성년자 계약은 법정대리인 동의가 필요해요",
                explanation=f"만 {ctx.age}세는 미성년자라서 부모님 등 법정대리인의 동의 없이 한 계약은 취소할 수 있어요.",
                law_name=limit.law_name,
                article=limit.article,
                field_key=None,
                matched={"age": ctx.age},
                action="계약 전에 법정대리인 동의서를 받거나, 이미 계약했다면 취소할 수 있는지 상담하세요.",
            )
        )


def _youth_hours(ctx: RuleContext, out: RuleResult) -> None:
    if ctx.doc_type != "EMPLOYMENT_CONTRACT":
        return
    if ctx.age is None:
        out.skipped.append("YOUTH_WORK_HOURS")
        return
    limit_age = ctx.thresholds.get("YOUTH_WORKER_AGE", ctx.base_date)
    if not limit_age or ctx.age >= limit_age.value:
        return
    daily, weekly = ctx.number("workHoursPerDay"), ctx.number("workHoursPerWeek")
    max_daily = ctx.thresholds.get("YOUTH_DAILY_HOURS", ctx.base_date)
    max_weekly = ctx.thresholds.get("YOUTH_WEEKLY_HOURS", ctx.base_date)
    over_daily = daily is not None and max_daily and daily > max_daily.value
    over_weekly = weekly is not None and max_weekly and weekly > max_weekly.value
    if over_daily or over_weekly:
        out.issues.append(
            RuleIssue(
                code="YOUTH_WORK_HOURS",
                severity="HIGH",
                title="연소근로자 근로시간 한도를 넘어요",
                explanation=f"만 18세 미만은 1일 {int(max_daily.value)}시간, 1주 {int(max_weekly.value)}시간을 넘겨 일할 수 없어요 (합의 시 1일 1시간, 1주 5시간까지 연장).",
                law_name=limit_age.law_name,
                article=limit_age.article,
                field_key="workHoursPerDay" if over_daily else "workHoursPerWeek",
                matched={"age": ctx.age, "workHoursPerDay": daily, "workHoursPerWeek": weekly},
                action="근로시간을 줄이거나 고용노동부(1350)에 상담하세요.",
            )
        )


def _minimum_wage(ctx: RuleContext, out: RuleResult) -> None:
    if ctx.doc_type != "EMPLOYMENT_CONTRACT":
        return
    wage = ctx.thresholds.get("MINIMUM_WAGE_HOURLY", ctx.base_date)
    if not wage:
        out.skipped.append("MINIMUM_WAGE")
        return
    hourly = ctx.amount("hourlyWage")
    field_key = "hourlyWage"
    if hourly is None:
        monthly, weekly = ctx.amount("monthlySalary"), ctx.number("workHoursPerWeek")
        if monthly is None or not weekly:
            return
        # 월 환산 근로시간 = (주 근로시간 + 주휴시간) × 365 ÷ 7 ÷ 12, 반올림 (주 40시간 → 209시간)
        holiday = min(weekly / 5, 8) if weekly >= 15 else 0
        monthly_hours = round((weekly + holiday) * 365 / 7 / 12)
        hourly = int(monthly / monthly_hours)
        field_key = "monthlySalary"
    if hourly < wage.value:
        out.issues.append(
            RuleIssue(
                code="MINIMUM_WAGE",
                severity="HIGH",
                title="최저임금보다 적은 임금이에요",
                explanation=f"{ctx.base_date.year}년 최저임금은 시간당 {format_won(int(wage.value))}인데, 이 계약은 시간당 약 {format_won(hourly)}이에요.",
                law_name=wage.law_name,
                article=wage.article,
                field_key=field_key,
                matched={"hourlyWage": hourly, "minimumWage": int(wage.value)},
                action="임금을 최저임금 이상으로 고쳐 달라고 요청하고, 어려우면 고용노동부(1350)에 상담하세요.",
            )
        )


def _priority_repayment(ctx: RuleContext, out: RuleResult) -> None:
    if ctx.doc_type != "LEASE_CONTRACT":
        return
    deposit = ctx.amount("deposit")
    if deposit is None:
        return
    region = (ctx.profile or {}).get("region") or {}
    group = region_group(region.get("sido"), region.get("sigungu"))
    if group is None:
        out.skipped.append("PRIORITY_REPAYMENT")
        return
    rule = ctx.thresholds.get("PRIORITY_REPAYMENT", ctx.base_date, group)
    if not rule:
        return
    area = f"{region.get('sido', '')} {region.get('sigungu', '')}".strip()
    if deposit <= rule.value:
        out.issues.append(
            RuleIssue(
                code="PRIORITY_REPAYMENT",
                severity="LOW",
                title="보증금 일부를 먼저 돌려받을 수 있는 대상이에요",
                explanation=f"{area} 기준 보증금 {format_won(int(rule.value))} 이하는 집이 경매로 넘어가도 최대 {format_won(int(rule.value2 or 0))}까지 먼저 돌려받을 수 있어요. 전입신고를 해 둬야 해요.",
                law_name=rule.law_name,
                article=rule.article,
                field_key="deposit",
                matched={"deposit": deposit, "limit": int(rule.value), "regionGroup": group},
                action="입주하면 바로 전입신고를 하고 확정일자를 받으세요.",
            )
        )
    else:
        out.issues.append(
            RuleIssue(
                code="PRIORITY_REPAYMENT",
                severity="MEDIUM",
                title="최우선변제 대상이 아닌 보증금이에요",
                explanation=f"{area} 기준 최우선변제 한도({format_won(int(rule.value))})보다 보증금이 커서, 집에 문제가 생기면 순서에 따라 돌려받아요.",
                law_name=rule.law_name,
                article=rule.article,
                field_key="deposit",
                matched={"deposit": deposit, "limit": int(rule.value), "regionGroup": group},
                action="등기부등본으로 선순위 대출을 확인하고, 확정일자와 전세보증보험 가입을 검토하세요.",
            )
        )


def _interest_cap(ctx: RuleContext, out: RuleResult) -> None:
    rate = ctx.number("interestRate")
    if rate is None:
        return
    ratio = rate / 100 if rate > 1 else rate
    cap = ctx.thresholds.get("INTEREST_RATE_CAP", ctx.base_date)
    if cap and ratio > cap.value:
        out.issues.append(
            RuleIssue(
                code="INTEREST_RATE_CAP",
                severity="HIGH",
                title="법정 최고 이자율을 넘어요",
                explanation=f"연 이자율 {ratio * 100:.1f}%는 법정 최고 이자율 연 {cap.value * 100:.0f}%를 넘어요. 넘는 부분은 무효예요.",
                law_name=cap.law_name,
                article=cap.article,
                field_key="interestRate",
                matched={"interestRate": ratio, "cap": cap.value},
                action="넘는 이자는 내지 않아도 돼요. 이미 냈다면 돌려받을 수 있는지 상담하세요.",
                needs_expert=True,
            )
        )


RULES = [_minor_contract, _youth_hours, _minimum_wage, _priority_repayment, _interest_cap]


def evaluate(ctx: RuleContext) -> RuleResult:
    out = RuleResult()
    for rule in RULES:
        rule(ctx, out)
    return out
