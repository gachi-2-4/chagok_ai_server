import io
import json

from PIL import Image

from tests.conftest import user

LEASE_TEXT = (
    "주택 임대차 계약서\n임대인 홍길동 임차인 김차곡\n임대보증금 금 일천만원정(₩10,000,000)\n"
    "차임은 월 금 오십오만원정으로 하고 매월 25일에 지불한다\n임대차 기간 2026년 12월 1일부터 2028년 11월 30일까지\n"
    "제5조 중도 해지 시 잔여 기간 월세의 10%를 위약금으로 한다\n임대인 서명 (인) 임차인 서명 (인)"
)


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    return buf.getvalue()


def ocr_result(text: str) -> str:
    lines = text.split("\n")
    blocks = [{"text": line, "bbox": [0.1, 0.05 * i, 0.8, 0.04], "confidence": 0.95} for i, line in enumerate(lines)]
    return json.dumps({"pages": [{"page": 1, "text": text, "blocks": blocks}]}, ensure_ascii=False)


EXTRACTION = {
    "title": "주택 임대차 계약서",
    "summary": "보증금 1,000만원, 월세 55만원, 매월 25일 납부.",
    "parties": [{"role": "lessor", "name": "홍길동"}, {"role": "LESSEE", "name": "김차곡"}],
    "fields": [
        {"key": "deposit", "label": "보증금", "originalLabel": "임대보증금", "valueType": "AMOUNT", "value": "10,000,000",
         "penaltyRate": None, "penaltyAmount": None, "evidence": "임대보증금 금 일천만원정(₩10,000,000)", "page": 1, "confidence": 0.98, "inferred": False},
        {"key": "monthlyRent", "label": "월세", "originalLabel": "차임", "valueType": "AMOUNT", "value": "550000",
         "penaltyRate": None, "penaltyAmount": None, "evidence": "차임은 월 금 오십오만원정으로 하고 매월 25일에 지불한다", "page": 1, "confidence": 0.95, "inferred": False},
        {"key": "contractEndDate", "label": "계약 만료일", "originalLabel": "임대차 기간", "valueType": "DATE", "value": "2028-11-30",
         "penaltyRate": None, "penaltyAmount": None, "evidence": "2028년 11월 30일까지", "page": 1, "confidence": 0.97, "inferred": False},
        {"key": "earlyTerminationPenalty", "label": "중도 해지 위약금", "originalLabel": "제5조", "valueType": "PENALTY",
         "value": "중도 해지 시 잔여 월세의 10%", "penaltyRate": 10, "penaltyAmount": None,
         "evidence": "제5조 중도 해지 시 잔여 기간 월세의 10%를 위약금으로 한다", "page": 1, "confidence": 0.62, "inferred": False},
        {"key": "maintenanceFee", "label": "관리비", "originalLabel": "관리비", "valueType": "AMOUNT", "value": "70000",
         "penaltyRate": None, "penaltyAmount": None, "evidence": "관리비 월 칠만원", "page": 1, "confidence": 0.9, "inferred": False},
    ],
    "paymentSchedules": [
        {"fieldKey": "monthlyRent", "label": "월세 납부", "recurrence": "MONTHLY", "dayOfMonth": 25, "startDate": "2026-12-25", "endDate": "2028-11-25"}
    ],
    "calendarEvents": [
        {"type": "PAYMENT", "fieldKey": "monthlyRent", "title": "월세 55만원 납부", "date": "2026-12-25", "remindBeforeDays": [3, 1]},
        {"type": "ACTION", "fieldKey": "contractEndDate", "title": "계약 갱신 여부 통보", "date": "2028-09-30", "remindBeforeDays": []},
        {"type": "EXPIRY", "fieldKey": "contractEndDate", "title": "임대차 계약 만료", "date": "2028-11-30", "remindBeforeDays": [60, 30, 7]},
        {"type": "EXPIRY", "fieldKey": "contractEndDate", "title": "중복", "date": "2028-11-30", "remindBeforeDays": [7]},
    ],
}


def post_analysis(client, **form):
    data = {"userId": "1024", "ocrResult": ocr_result(LEASE_TEXT)}
    data.update(form)
    return client.post(
        "/ai/documents/520/analysis?baseDate=2026-11-18",
        files={"file": ("lease.png", png_bytes(), "image/png")},
        data=data,
    )


def test_analysis_full(client, be, llm):
    be.users[1024] = user()
    llm.responses["_LLMExtraction"] = EXTRACTION
    llm.responses["_LLMRelevance"] = {"level": "RELEVANT", "reasons": ["임차인으로 계약한 서류예요."]}

    res = post_analysis(client)
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["documentId"] == 520
    assert body["docType"] == "LEASE_CONTRACT" and body["docCategory"] == "CONTRACT"
    assert body["fullText"].startswith("[1페이지]")
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["deposit"]["value"] == 10_000_000
    assert fields["deposit"]["evidenceLocation"]["bbox"] is not None
    assert fields["earlyTerminationPenalty"]["value"]["rate"] == 0.1
    assert fields["earlyTerminationPenalty"]["needsReview"] is True
    # 원문에 없는 근거(관리비)는 신뢰도를 낮추고 확인 필요로 표시
    assert fields["maintenanceFee"]["confidence"] <= 0.5 and fields["maintenanceFee"]["needsReview"]
    assert body["needsReview"] is True

    events = body["calendarEvents"]
    assert [e["type"] for e in events] == ["PAYMENT", "ACTION", "EXPIRY"]  # 중복 제거, 날짜순
    assert events[0]["rrule"] == "FREQ=MONTHLY;BYMONTHDAY=25;UNTIL=20281125"
    assert events[1]["remindBeforeDays"] == [14, 7, 1]
    assert body["conditions"]["paymentSchedules"][0]["fieldKey"] == "monthlyRent"
    assert body["parties"][0]["role"] == "LESSOR"

    # 서울 보증금 1,000만원 → 최우선변제 대상 (규칙 엔진)
    legal = body["legalCheck"]
    assert legal["issues"][0]["source"] == "RULE"
    assert legal["issues"][0]["laws"][0]["lawName"] == "주택임대차보호법 시행령"
    assert legal["disclaimer"]
    assert "LAW_DB_UNAVAILABLE" in body["warnings"]

    assert body["userRelevance"]["level"] == "RELEVANT"
    p = body["priority"]
    assert p["keyCost"] == {"fieldKey": "deposit", "label": "보증금", "amount": 10_000_000}
    assert p["keyDeadline"]["date"] == "2028-11-30" and p["keyDeadline"]["actionDate"] == "2028-09-30"


def test_analysis_without_overseas_consent_does_not_send_profile(client, be, llm):
    be.users[1024] = user(overseas=False)
    llm.responses["_LLMExtraction"] = EXTRACTION

    res = post_analysis(client)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["userRelevance"] is None
    assert "LLM_REVIEW_NO_OVERSEAS_CONSENT" in body["legalCheck"]["skippedRules"]
    sent = "\n".join(content for _, content in llm.calls)
    assert "2001-03-14" not in sent and "마포구" not in sent


def test_analysis_profile_missing_skips_legal(client, be, llm):
    be.users[1024] = user(profile=False)
    llm.responses["_LLMExtraction"] = EXTRACTION
    body = post_analysis(client).json()
    assert body["legalCheck"] is None
    assert body["userRelevance"] is None


def test_minor_contract_rule(client, be, llm):
    be.users[1024] = user(age_birth="2010-05-01", overseas=False)
    llm.responses["_LLMExtraction"] = EXTRACTION
    body = post_analysis(client).json()
    codes = [i["title"] for i in body["legalCheck"]["issues"]]
    assert any("미성년자" in t for t in codes)
    assert body["legalCheck"]["riskLevel"] == "HIGH"
    assert body["priority"]["level"] == "HIGH"


def test_analysis_errors(client, be, llm):
    be.users[1024] = user()
    res = client.post("/ai/documents/520/analysis", files={"file": ("a.txt", b"hi", "text/plain")}, data={"userId": "1024"})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_FILE"

    res = client.post("/ai/documents/520/analysis", files={"file": ("a.png", png_bytes(), "image/png")}, data={"userId": "1024"})
    assert res.status_code == 422 and res.json()["code"] == "TEXT_NOT_RECOGNIZED"

    res = client.post(
        "/ai/documents/520/analysis",
        files={"file": ("a.png", png_bytes(), "image/png")},
        data={"userId": "1024"},
        headers={"X-API-Key": "wrong"},
    )
    assert res.status_code == 401 and res.json()["code"] == "INVALID_API_KEY"

    llm.responses["_LLMExtraction"] = EXTRACTION
    res = post_analysis(client, userId="9999")
    assert res.status_code == 404 and res.json()["code"] == "USER_NOT_FOUND"


def test_text_pdf_is_read_without_ocr(client, be, llm):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 80), "Lease contract deposit 10,000,000 monthly rent 550,000 signature", fontsize=11)
    pdf = doc.tobytes()

    be.users[1024] = user(overseas=False)
    llm.responses["_LLMClassification"] = {"docType": "LEASE_CONTRACT", "docCategory": "CONTRACT", "confidence": 0.8, "reasons": ["보증금"]}
    llm.responses["_LLMExtraction"] = {**EXTRACTION, "fields": [], "paymentSchedules": [], "calendarEvents": []}
    res = client.post("/ai/documents/7/analysis", files={"file": ("c.pdf", pdf, "application/pdf")}, data={"userId": "1024"})
    assert res.status_code == 200, res.text
    assert "deposit 10,000,000" in res.json()["fullText"]
    assert res.json()["pageCount"] == 1
