from tests.conftest import user


def lease_doc(owner=1024, **extra):
    doc = {
        "_owner": owner,
        "documentId": 501,
        "title": "임대차계약서",
        "status": "CONFIRMED",
        "docType": "LEASE_CONTRACT",
        "docCategory": "CONTRACT",
        "updatedAt": "2026-11-10T13:05:24+09:00",
        "summary": "보증금 2억",
        "parties": [{"role": "LESSOR", "name": "홍길동"}, {"role": "LESSEE", "name": "이차곡"}],
        "fields": [
            {"key": "deposit", "label": "보증금", "value": 230000000, "valueType": "AMOUNT", "evidence": "보증금 이억삼천만원",
             "evidenceLocation": {"page": 1, "bbox": [0.1, 0.3, 0.6, 0.04]}, "userEdited": False},
            {"key": "monthlyRent", "label": "월세", "value": 500000, "valueType": "AMOUNT", "evidence": "차임은 매월 25일에 지급하기로 한다",
             "evidenceLocation": {"page": 1, "bbox": [0.1, 0.48, 0.78, 0.05]}, "userEdited": True},
            {"key": "contractStartDate", "label": "시작일", "value": "2025-03-01", "valueType": "DATE"},
            {"key": "contractEndDate", "label": "만료일", "value": "2027-02-28", "valueType": "DATE"},
            {"key": "earlyTerminationPenalty", "label": "위약금", "value": {"description": "잔여 월세 10%"}, "valueType": "PENALTY"},
            {"key": "propertyAddress", "label": "소재지", "value": "마포구 성산동 행복빌라 302호", "valueType": "TEXT"},
        ],
        "fullText": "[1페이지]\n주택 임대차 계약서\n보증금 이억삼천만원\n차임은 매월 25일에 지급하기로 한다.",
        "legalCheck": None,
    }
    doc.update(extra)
    return doc


# ---------- 요약 카드 ----------

def test_summary_card_uses_only_document_values(client, be, llm):
    be.documents[501] = lease_doc()
    llm.responses["_LLMCard"] = {
        "title": "전세 임대차 계약서",
        "oneLineSummary": "보증금 2억 3천만 원, 2년 계약이에요.",
        "keyValues": [
            {"label": "임대인", "source": "PARTY", "fieldKeys": [], "partyRole": "LESSOR"},
            {"label": "소재지", "source": "FIELD", "fieldKeys": ["propertyAddress"], "partyRole": None},
            {"label": "보증금", "source": "FIELD", "fieldKeys": ["deposit"], "partyRole": None},
            {"label": "계약기간", "source": "PERIOD", "fieldKeys": ["contractStartDate", "contractEndDate"], "partyRole": None},
            {"label": "없는값", "source": "FIELD", "fieldKeys": ["nothing"], "partyRole": None},
        ],
        "specialTerms": ["반려동물은 사전 동의 후 가능"],
        "checklist": [{"checkKey": "moveInReport", "text": "전입신고 완료", "done": False}],
    }
    res = client.post("/ai/documents/summary-card", json={"documentId": 501, "userId": 1024})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["category"] == "집 · 임대"
    kv = {i["label"]: i for i in body["sections"][0]["items"]}
    assert kv["임대인"] == {"label": "임대인", "value": "홍○○", "fieldKey": None, "text": None, "checkKey": None, "done": None}
    assert kv["소재지"]["value"] == "마포구 성산동 ○○빌라 302호"
    assert kv["보증금"]["value"] == "230,000,000원" and kv["보증금"]["fieldKey"] == "deposit"
    assert kv["계약기간"]["value"] == "2025.03.01 – 2027.02.28" and kv["계약기간"]["fieldKey"] is None
    assert "없는값" not in kv
    assert [s["type"] for s in body["sections"]] == ["KEY_VALUE", "BULLET", "CHECKLIST"]

    # 같은 updatedAt이면 캐시
    client.post("/ai/documents/summary-card", json={"documentId": 501, "userId": 1024})
    assert sum(1 for name, _ in llm.calls if name == "_LLMCard") == 1


def test_summary_card_other_users_document(client, be):
    be.documents[501] = lease_doc(owner=1)
    res = client.post("/ai/documents/summary-card", json={"documentId": 501, "userId": 1024})
    assert res.status_code == 404 and res.json()["code"] == "DOCUMENT_NOT_FOUND"


# ---------- 질문 칩 ----------

def test_suggestions(client, be, llm):
    be.documents[501] = lease_doc()
    res = client.post("/ai/chat/suggestions", json={"chatId": 12, "userId": 1024, "documentId": 501, "count": 3})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["chatId"] == 12 and len(body["suggestions"]) == 3
    assert body["suggestions"][0]["text"] == "월세는 언제까지 내요?"
    assert all(len(s["text"]) <= 20 for s in body["suggestions"])
    assert llm.calls == []  # LLM 호출 없음


# ---------- 채팅 ----------

def test_chat_answer(client, be, llm):
    be.users[1024] = user()
    be.documents[501] = lease_doc()
    be.messages[12] = [{"role": "USER", "content": "보증금 얼마야?"}, {"role": "ASSISTANT", "content": "2억 3천만 원이에요."}]
    llm.responses["_LLMAnswer"] = {
        "answer": "매월 **25일**까지 **500,000원**을 내면 돼요.",
        "answerType": "ANSWERED",
        "citations": [
            {"sourceType": "DOCUMENT", "page": 1, "snippet": "차임은 매월 25일에 지급하기로 한다", "lawRef": None},
            {"sourceType": "DOCUMENT", "page": 2, "snippet": "원문에 없는 문장입니다 지어낸 근거", "lawRef": None},
            {"sourceType": "LAW", "page": None, "snippet": "", "lawRef": 3},
        ],
        "followUpQuestions": ["늦게 내면 어떻게 되나요?", "b", "c"],
    }
    res = client.post("/ai/chats/12/answer", json={"userId": 1024, "documentId": 501, "question": "월세는 언제 내야 하나요?"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["citations"]) == 1  # 지어낸 근거와 없는 법 인용은 버림
    c = body["citations"][0]
    assert c["documentId"] == 501 and c["bbox"] == [0.1, 0.48, 0.78, 0.05]
    assert body["disclaimer"] is None
    assert len(body["followUpQuestions"]) == 2
    content = llm.calls[-1][1]
    assert content.startswith("[서류]")  # 서류를 앞에 고정 (입력 캐시)
    assert "사용자가 고친 값" in content and "[지난 대화]" in content
    assert "2001-03-14" not in content


def test_chat_rate_limit_and_validation(client, be, llm, settings):
    be.users[1024] = user()
    be.documents[501] = lease_doc()
    llm.responses["_LLMAnswer"] = {"answer": "네", "answerType": "ANSWERED", "citations": [], "followUpQuestions": []}
    res = client.post("/ai/chats/12/answer", json={"userId": 1024, "documentId": 501, "question": "x" * 501})
    assert res.status_code == 400 and res.json()["code"] == "INVALID_REQUEST"
    codes = [
        client.post("/ai/chats/12/answer", json={"userId": 1024, "documentId": 501, "question": "질문"}).status_code
        for _ in range(settings.chat_rate_per_minute + 1)
    ]
    assert codes[-1] == 429


def test_chat_document_not_ready(client, be, llm):
    be.users[1024] = user()
    be.documents[501] = lease_doc(status="ANALYZING")
    res = client.post("/ai/chats/12/answer", json={"userId": 1024, "documentId": 501, "question": "질문"})
    assert res.status_code == 409


# ---------- 서류 비교 ----------

def contract_and_bill(be):
    be.documents[501] = {
        "_owner": 1024, "documentId": 501, "status": "CONFIRMED", "title": "통신 계약서", "docType": "SERVICE_CONTRACT", "docCategory": "CONTRACT",
        "parties": [{"role": "PROVIDER", "name": "OO통신"}],
        "fields": [{"key": "monthlyFee", "label": "월 이용료", "value": 33000, "valueType": "AMOUNT", "evidence": "월 이용료 33,000원(VAT 포함)",
                    "evidenceLocation": {"page": 1, "bbox": [0.1, 0.4, 0.55, 0.03]}}],
    }
    be.documents[511] = {
        "_owner": 1024, "documentId": 511, "status": "CONFIRMED", "title": "11월 통신요금 고지서", "docType": "BILL", "docCategory": "BILL",
        "parties": [{"role": "ISSUER", "name": "OO통신"}],
        "fields": [
            {"key": "billingMonth", "label": "청구 월", "value": "2026-11", "valueType": "TEXT"},
            {"key": "baseFee", "label": "기본료", "value": 40000, "valueType": "AMOUNT", "evidence": "기본료 40,000원"},
            {"key": "extraUsageFee", "label": "부가사용료", "value": 10000, "valueType": "AMOUNT", "evidence": "부가사용료 10,000원"},
            {"key": "discountAmount", "label": "할인", "value": 5000, "valueType": "AMOUNT", "evidence": "약정할인 5,000원"},
        ],
    }


COMPARE_LLM = {
    "comparable": True,
    "notComparableReason": "NONE",
    "amountPairs": [
        {"title": "통신요금 비교", "itemKey": "monthlyFee", "baseKeys": [{"fieldKey": "monthlyFee", "sign": 1}],
         "targetKeys": [{"fieldKey": "baseFee", "sign": 1}, {"fieldKey": "extraUsageFee", "sign": 1}, {"fieldKey": "discountAmount", "sign": -1},
                        {"fieldKey": "notExists", "sign": 1}]}
    ],
    "conditionDiffs": [],
    "notices": [{"severity": "WARNING", "title": "확인 필요 · 약정 금액보다 높아요.", "description": "추가 사용료를 확인해 주세요.", "possibleReasons": ["EXTRA_USAGE"]}],
}


def test_comparison_bill(client, be, llm):
    contract_and_bill(be)
    llm.responses["_LLMCompare"] = COMPARE_LLM
    res = client.post("/ai/comparisons", json={"comparisonId": 301, "userId": 1024, "baseDocumentId": 501, "targetDocumentId": 511, "compareType": "BILL"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "MISMATCH"
    item = body["comparisons"][0]
    assert item["target"] == {"label": "11월 고지서", "value": "45,000원", "amount": 45000}  # 코드로 계산 40,000+10,000-5,000
    assert item["difference"]["value"] == "+12,000원"
    assert body["highlight"]["formula"] == "청구액 45,000원 - 약정액 33,000원"
    assert {e["documentId"] for e in item["evidence"]} == {501, 511}
    assert body["notices"][0]["severity"] == "WARNING"


def test_comparison_errors(client, be, llm):
    contract_and_bill(be)
    res = client.post("/ai/comparisons", json={"comparisonId": 1, "userId": 1024, "baseDocumentId": 501, "targetDocumentId": 501, "compareType": "BILL"})
    assert res.status_code == 400
    res = client.post("/ai/comparisons", json={"comparisonId": 1, "userId": 1024, "baseDocumentId": 501, "targetDocumentId": 511, "compareType": "AD"})
    assert res.status_code == 422 and res.json()["reason"] == "WRONG_DOCUMENT_TYPE"
    llm.responses["_LLMCompare"] = {**COMPARE_LLM, "comparable": False, "notComparableReason": "DIFFERENT_PROVIDER"}
    res = client.post("/ai/comparisons", json={"comparisonId": 1, "userId": 1024, "baseDocumentId": 501, "targetDocumentId": 511, "compareType": "BILL"})
    assert res.status_code == 422 and res.json() == {"code": "NOT_COMPARABLE", "message": "같은 서비스와 같은 기간의 서류를 골라 주세요.", "reason": "DIFFERENT_PROVIDER"}


# ---------- 확인 요청 문자 ----------

def saved_comparison():
    return {
        "comparisonId": 301, "status": "MISMATCH", "baseDocumentId": 501, "targetDocumentId": 511,
        "comparisons": [{"title": "통신요금", "base": {"label": "계약서", "value": "33,000원"}, "target": {"label": "11월 고지서", "value": "45,000원"},
                         "difference": {"label": "차이", "value": "+12,000원"}}],
        "highlight": {"value": "12,000원"},
    }


def test_inquiry_message(client, be, llm):
    contract_and_bill(be)
    be.comparisons[301] = saved_comparison()
    good = "안녕하세요.\n11월 통신요금 확인 부탁드립니다.\n\n계약서상 월 요금은 33,000원인데 고지서에는 45,000원이 청구되어 12,000원 차이가 있습니다.\n\n산정 근거를 알려 주실 수 있을까요?"
    answers = iter([{"text": "50,000원 차이가 있습니다."}, {"text": good}])  # 첫 번째는 금액이 틀려서 다시 생성
    llm.responses["_LLMMessage"] = lambda content: next(answers)
    res = client.post("/ai/comparisons/301/inquiry-messages", json={"userId": 1024, "tone": "POLITE"})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["message"]["text"] == good
    assert body["message"]["type"] == "LMS" and body["message"]["byteLength"] > 90
    assert "OO통신" in llm.calls[-1][1]


def test_inquiry_fallback_and_no_difference(client, be, llm):
    be.comparisons[301] = saved_comparison()
    llm.responses["_LLMMessage"] = {"text": "99,999원 차이"}
    body = client.post("/ai/comparisons/301/inquiry-messages", json={"userId": 1024}).json()
    assert "33,000원" in body["message"]["text"] and "99,999" not in body["message"]["text"]

    be.comparisons[302] = {**saved_comparison(), "status": "MATCH"}
    res = client.post("/ai/comparisons/302/inquiry-messages", json={"userId": 1024})
    assert res.status_code == 422 and res.json()["code"] == "NO_DIFFERENCE"
