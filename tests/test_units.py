from datetime import date

from app.legal.rules import RuleContext, evaluate
from app.legal.thresholds import ThresholdTable, region_group
from app.schemas.analysis import Field
from app.utils.text import calc_age, cp949_length, format_won, format_won_readable, mask_name, mask_sensitive, parse_amount
from scripts.sync_laws import parse_articles


def test_parse_amount():
    assert parse_amount("₩10,000,000") == 10_000_000
    assert parse_amount("금 일천만원정(10000000)") == 10_000_000
    assert parse_amount("2억 3천만") == 230_000_000
    assert parse_amount("55만") == 550_000
    assert parse_amount("-5,000원") == -5000
    assert parse_amount("") is None


def test_format_and_mask():
    assert format_won(12000, signed=True) == "+12,000원"
    assert format_won_readable(230_000_000) == "2억 3천만 원"
    assert mask_name("홍길동") == "홍○○"
    assert mask_sensitive("주민번호 010203-3123456 계좌 123-456-789012") == "주민번호 010203-3****** 계좌 123-***-******"
    assert cp949_length("가a") == 3


def test_calc_age():
    assert calc_age("2008-11-21", date(2026, 11, 20)) == 17
    assert calc_age("2008-11-20", date(2026, 11, 20)) == 18
    assert calc_age(None, date(2026, 1, 1)) is None


def test_region_group():
    assert region_group("서울특별시", "마포구") == "SEOUL"
    assert region_group("경기도", "수원시 팔달구") == "OVERCROWDED"
    assert region_group("인천광역시", "강화군") == "OTHER"
    assert region_group("부산광역시", "해운대구") == "METRO"
    assert region_group("부산광역시", "기장군") == "OTHER"
    assert region_group("강원특별자치도", "춘천시") == "OTHER"


def _field(key, value, kind="AMOUNT"):
    return Field(key=key, label=key, originalLabel=key, value=value, valueType=kind, evidence=None, evidenceLocation=None,
                 confidence=1, inferred=False, needsReview=False)


def test_minimum_wage_rule_uses_effective_date():
    table = ThresholdTable()
    fields = {"hourlyWage": _field("hourlyWage", 10100)}
    base = dict(doc_type="EMPLOYMENT_CONTRACT", doc_category="CONTRACT", fields=fields, age=20, profile={}, consents={}, thresholds=table)
    assert [i.code for i in evaluate(RuleContext(base_date=date(2025, 6, 1), **base)).issues] == []  # 2025년 10,030원
    assert [i.code for i in evaluate(RuleContext(base_date=date(2026, 6, 1), **base)).issues] == ["MINIMUM_WAGE"]  # 2026년 10,320원


def test_monthly_salary_converted_to_hourly():
    fields = {"monthlySalary": _field("monthlySalary", 2_000_000), "workHoursPerWeek": _field("workHoursPerWeek", 40, "NUMBER")}
    ctx = RuleContext(doc_type="EMPLOYMENT_CONTRACT", doc_category="CONTRACT", fields=fields, age=25, profile={}, consents={}, base_date=date(2026, 3, 1))
    issue = evaluate(ctx).issues[0]
    assert issue.code == "MINIMUM_WAGE" and issue.matched["hourlyWage"] == 9569  # 2,000,000 / 209h


def test_youth_hours_and_skipped_age():
    fields = {"workHoursPerDay": _field("workHoursPerDay", 8, "NUMBER")}
    ctx = RuleContext(doc_type="EMPLOYMENT_CONTRACT", doc_category="CONTRACT", fields=fields, age=17, profile={}, consents={}, base_date=date(2026, 3, 1))
    assert "YOUTH_WORK_HOURS" in [i.code for i in evaluate(ctx).issues]
    ctx.age = None
    assert set(evaluate(ctx).skipped) == {"MINOR_CONTRACT", "YOUTH_WORK_HOURS"}


def test_parse_law_articles():
    law = {
        "조문": {
            "조문단위": [
                {"조문번호": "1", "조문여부": "전문", "조문내용": "제1장 총칙"},
                {"조문번호": "3", "조문여부": "조문", "조문제목": "적용 범위", "조문내용": "제3조(적용 범위) 이 법은 ...", "조문시행일자": "20230221"},
                {"조문번호": "8", "조문가지번호": "2", "조문여부": "조문", "조문제목": "보증금",
                 "조문내용": "제8조의2(보증금)", "항": [{"항번호": "①", "항내용": "① 첫째 항"}, {"항번호": "②", "항내용": "② 둘째 항", "호": [{"호내용": "1. 호 내용"}]}]},
            ]
        }
    }
    articles = parse_articles(law)
    assert [(a.article_no, a.paragraph_no) for a in articles] == [("제3조", None), ("제8조의2", None), ("제8조의2", "①"), ("제8조의2", "②")]
    assert articles[0].effective_date == date(2023, 2, 21)
    assert "1. 호 내용" in articles[3].content
