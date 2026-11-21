#!/usr/bin/env bash
# 실행 중인 서버에 샘플 계약서를 보내 문서 분석을 시험합니다 (실제 OpenAI 호출, 약 1~2센트).
#   bash scripts/smoke_test.sh [서버 주소, 기본 http://localhost:8000]
# API 키는 .env 의 AI_API_KEY 를 읽습니다 (화면에 출력하지 않음).
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${1:-http://localhost:8000}"
KEY=$(grep -E '^AI_API_KEY=' .env | cut -d= -f2-)

echo "== health"; curl -s "$BASE/health"; echo
echo "== 문서 분석 (samples/sample_lease.pdf)"
curl -s -m 120 -X POST "$BASE/ai/documents/520/analysis" \
  -H "X-API-Key: $KEY" \
  -F "file=@samples/sample_lease.pdf;type=application/pdf" \
  -F "userId=1024" > /tmp/chagok_smoke.json

PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" - <<'EOF'
import json
b = json.load(open("/tmp/chagok_smoke.json", encoding="utf-8"))
if "code" in b:
    print("FAILED:", b); raise SystemExit(1)
print("docType:", b["docType"], "| title:", b["title"])
for f in b["fields"]:
    print(f"  {f['key']:<26} {str(f['value'])[:40]:<42} 확인필요={f['needsReview']}")
for e in b["calendarEvents"]:
    print(f"  일정 {e['type']:<8} {e['date']} {e['title']}")
print("중요도:", b["priority"]["level"], "| 경고:", b["warnings"])
print("OK")
EOF
