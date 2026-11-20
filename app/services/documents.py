"""BE 서류 상세 조회 응답을 다루는 공통 함수."""

from app.core.errors import AppError
from app.utils.text import format_date_dot, format_won


def require_analyzed(doc: dict) -> dict:
    if doc.get("status") in ("ANALYZING", "FAILED"):
        raise AppError(409, "DOCUMENT_NOT_READY", "서류 분석이 끝나지 않았습니다.")
    return doc


def fields_by_key(doc: dict) -> dict[str, dict]:
    return {f["key"]: f for f in doc.get("fields") or [] if f.get("key")}


def display_value(field: dict) -> str:
    """field 값을 화면 표기로 (금액: 콤마+원, 날짜: YYYY.MM.DD)."""
    value = field.get("value")
    kind = field.get("valueType")
    if value is None:
        return ""
    if kind == "AMOUNT" and isinstance(value, (int, float)):
        return format_won(int(value))
    if kind == "DATE":
        return format_date_dot(str(value))
    if kind == "PENALTY" and isinstance(value, dict):
        return value.get("description") or ""
    return str(value)


def fields_for_prompt(doc: dict) -> str:
    """LLM 입력용 항목 목록. 사용자가 고친 값(userEdited)을 원문보다 우선한다고 표시합니다."""
    lines = []
    for f in doc.get("fields") or []:
        mark = " (사용자가 고친 값, 원문보다 우선)" if f.get("userEdited") else ""
        lines.append(f"- {f.get('key')} | {f.get('label')}: {display_value(f)}{mark}")
    return "\n".join(lines) or "없음"
