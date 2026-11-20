"""서류 중요도 (코드 계산, LLM 호출 없음)."""

from datetime import date

from app.schemas.analysis import CalendarEvent, Field, KeyCost, KeyDeadline, LegalCheck, Priority, UserRelevance
from app.utils.text import format_won_readable

KEY_COST_ORDER = ["deposit", "billAmount", "totalAmount", "monthlySalary", "hourlyWage", "monthlyRent", "monthlyFee", "promotedPrice"]


def _key_cost(fields: list[Field]) -> KeyCost | None:
    amounts = {f.key: f for f in fields if f.valueType == "AMOUNT" and isinstance(f.value, int) and f.value > 0}
    if not amounts:
        return None
    chosen = next((amounts[k] for k in KEY_COST_ORDER if k in amounts), None) or max(amounts.values(), key=lambda f: f.value)
    return KeyCost(fieldKey=chosen.key, label=chosen.label, amount=chosen.value)


def _key_deadline(events: list[CalendarEvent], base_date: date) -> KeyDeadline | None:
    today = base_date.isoformat()
    main = [e for e in events if e.type in ("EXPIRY", "PAYMENT")]
    future = [e for e in main if e.date >= today or e.rrule]
    if not future:
        return None
    expiry = [e for e in future if e.type == "EXPIRY"]
    chosen = min(expiry or future, key=lambda e: e.date)
    actions = [e for e in events if e.type == "ACTION" and e.date <= chosen.date and (chosen.fieldKey is None or e.fieldKey == chosen.fieldKey)]
    action = min(actions, key=lambda e: e.date) if actions else None
    return KeyDeadline(
        fieldKey=chosen.fieldKey,
        label=chosen.title,
        date=chosen.date,
        actionLabel=action.title if action else None,
        actionDate=action.date if action else None,
    )


def _days_until(iso: str, base_date: date) -> int:
    return (date.fromisoformat(iso) - base_date).days


def build_priority(
    doc_category: str,
    fields: list[Field],
    events: list[CalendarEvent],
    legal: LegalCheck | None,
    relevance: UserRelevance | None,
    base_date: date,
) -> Priority:
    score = {"CONTRACT": 40, "BILL": 40, "INFO": 20, "AD": 5}[doc_category]
    reasons: list[str] = []

    cost = _key_cost(fields)
    if cost:
        amount = cost.amount
        score += 25 if amount >= 100_000_000 else 20 if amount >= 10_000_000 else 15 if amount >= 1_000_000 else 10
        reasons.append(f"{cost.label} {format_won_readable(amount)}이 걸린 서류예요.")

    recurring = [e for e in events if e.type == "PAYMENT" and e.rrule]
    if recurring:
        score += 10
        reasons.append(f"반복해서 내야 하는 돈이 있어요 ({recurring[0].title}).")

    upcoming = [e for e in events if e.date >= base_date.isoformat()]
    if upcoming:
        nearest = min(upcoming, key=lambda e: e.date)
        days = _days_until(nearest.date, base_date)
        if days <= 30:
            score += 20
            reasons.append(f"{days}일 안에 '{nearest.title}' 기한이 있어요.")
        elif days <= 90:
            score += 10

    if legal and legal.riskLevel in ("HIGH", "MEDIUM"):
        score += 25 if legal.riskLevel == "HIGH" else 10
        top = legal.issues[0]
        reasons.insert(0, f"법적으로 확인할 점이 있어요: {top.title}")

    score = max(0, min(100, score))
    level = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
    if legal and legal.riskLevel == "HIGH":
        level = "HIGH"
    if doc_category == "AD":
        level, score = "LOW", min(score, 30)
        reasons.insert(0, "광고·홍보물이에요.")
    if relevance and relevance.level == "NOT_RELEVANT":
        level, score = "LOW", min(score, 30)
        reasons.insert(0, "내 상황과 관련이 적은 서류예요.")

    return Priority(
        level=level,  # type: ignore[arg-type]
        score=score,
        reasons=reasons[:3],
        keyCost=cost,
        keyDeadline=_key_deadline(events, base_date),
    )
