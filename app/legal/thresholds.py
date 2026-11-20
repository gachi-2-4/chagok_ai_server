"""규칙 엔진 기준값 (시행일이 있는 표). 법령 DB가 있으면 DB 값을, 없으면 data/legal_thresholds.json 을 씁니다."""

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[2] / "data" / "legal_thresholds.json"


@dataclass
class Threshold:
    key: str
    region_group: str | None
    value: float
    value2: float | None
    effective_from: date
    effective_to: date | None
    law_name: str
    article: str
    note: str = ""


@lru_cache
def _load() -> tuple[list[Threshold], dict]:
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    rows = [
        Threshold(
            key=t["key"],
            region_group=t.get("regionGroup"),
            value=float(t["value"]),
            value2=float(t["value2"]) if t.get("value2") is not None else None,
            effective_from=date.fromisoformat(t["effectiveFrom"]),
            effective_to=date.fromisoformat(t["effectiveTo"]) if t.get("effectiveTo") else None,
            law_name=t["lawName"],
            article=t["article"],
            note=t.get("note", ""),
        )
        for t in data["thresholds"]
    ]
    return rows, data["regionGroups"]


class ThresholdTable:
    def __init__(self, rows: list[Threshold] | None = None):
        self.rows = rows if rows is not None else _load()[0]

    def get(self, key: str, on: date, region_group: str | None = None) -> Threshold | None:
        candidates = [
            r
            for r in self.rows
            if r.key == key
            and r.region_group == region_group
            and r.effective_from <= on
            and (r.effective_to is None or on <= r.effective_to)
        ]
        return max(candidates, key=lambda r: r.effective_from) if candidates else None


def region_group(sido: str | None, sigungu: str | None) -> str | None:
    """최우선변제 지역 구분. 과밀억제권역 일부 지역(남양주·시흥 일부 등)은 단순화했습니다."""
    if not sido:
        return None
    groups = _load()[1]
    sigungu = sigungu or ""
    if sido in groups["SEOUL"]["sido"]:
        return "SEOUL"
    overcrowded = groups["OVERCROWDED"]
    if sido == "인천광역시":
        return "OTHER" if sigungu in overcrowded["incheonExceptSigungu"] else "OVERCROWDED"
    if sido in overcrowded["sido"] or any(sigungu.startswith(s) for s in overcrowded["sigungu"]):
        return "OVERCROWDED"
    metro = groups["METRO"]
    if sido in metro["sido"]:
        return "OTHER" if sigungu.endswith("군") else "METRO"
    if any(sigungu.startswith(s) for s in metro["sigungu"]):
        return "METRO"
    return "OTHER"
