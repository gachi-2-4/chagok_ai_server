# chagok_ai_server

문서 자동 정리 서비스 **차곡**의 AI 서버입니다. 계약서·고지서·안내문을 분석하고, 서류 기반 채팅·비교·확인 요청 문자를 만듭니다.

- APP → BE → **AI 서버**. AI 서버는 BE DB에 접근하지 않고, 필요한 정보는 BE API를 `X-Internal-Key`로 호출해 가져옵니다.
- AI 서버는 서류 내용을 저장하지 않습니다. 문서 분석 응답의 `fullText`를 BE가 저장합니다.
- LLM: OpenAI (기본 `gpt-6-luna`, 모델명은 환경변수로 교체)
- 법령: AI 전용 법령 DB (PostgreSQL + pgvector), 국가법령정보센터 Open API에서 주 1회 동기화

## API

모든 요청에 `X-API-Key` 헤더가 필요합니다. 실행 후 `/docs`에서 스키마를 볼 수 있습니다.

| 기능 | Method | Path |
| --- | --- | --- |
| 문서 분석 (PDF·이미지 업로드) | POST | `/ai/documents/{id}/analysis` (multipart) |
| 쉬운말 요약 카드 | POST | `/ai/documents/summary-card` |
| 서류 기반 AI 채팅 | POST | `/ai/chats/{id}/answer` |
| 자주 묻는 질문 칩 | POST | `/ai/chat/suggestions` |
| 서류 비교 | POST | `/ai/comparisons` |
| 확인 요청 문자 생성 | POST | `/ai/comparisons/{id}/inquiry-messages` |
| 상태 확인 | GET | `/health` |

AI 서버가 호출하는 BE API: `GET /user/{userId}`, `GET /documents/{id}?userId=`, `GET /chats/{id}/messages?userId=&size=`, `GET /comparisons/{id}?userId=`

## 구조

```
app/
  main.py                 FastAPI 앱
  core/                   설정, 에러, API 키 인증, 의존성
  clients/                BE 호출(backend.py), OpenAI 호출(llm.py)
  ocr/                    PDF 텍스트 추출, 기기 OCR 결과 사용, 서버 OCR(선택)
  services/analysis/      문서 분석: 분류 → 추출 → 중요도 · 관련도
  legal/                  법적 점검: 규칙 엔진(rules.py) + 법령 RAG + LLM
  services/               요약 카드, 질문 칩, 채팅, 서류 비교, 확인 요청 문자
  routers/                API 경로
data/
  legal_thresholds.json   규칙 엔진 기준값 (최저임금, 최우선변제 등) ← 매년 확인
  target_laws.json        동기화할 법령 목록
db/schema.sql             법령 DB 스키마
scripts/sync_laws.py      국가법령정보센터 → 법령 DB 동기화
tests/
```

## 로컬 실행

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (mac/linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env          # 값 채우기
uvicorn app.main:app --reload
pytest
```

## 서버 배포

자세한 순서는 [DEPLOY.md](DEPLOY.md)를 보세요. 메모리 1GB 이하 서버는 `bash scripts/install_server.sh`로 Docker 없이 설치합니다.

## Docker (큰 서버)

```bash
cp .env.example .env            # 값 채우기
docker compose up -d --build    # AI 서버 + 법령 DB
docker compose exec ai python -m scripts.sync_laws   # 법령 동기화 (처음 한 번 + 주 1회)
```

주 1회 동기화는 서버 crontab에 등록합니다.

```
0 4 * * 1 cd /home/ubuntu/chagok_ai_server && docker compose exec -T ai python -m scripts.sync_laws >> sync.log 2>&1
```

## 법령 데이터

1. [open.law.go.kr](https://open.law.go.kr) 가입 → Open API 활용 신청 → 승인
2. 신청할 때 API를 호출할 **서버 IP**를 등록합니다 (등록 안 된 IP는 403). 공동활용 법령종류는 '대한민국 현행법령'의 목록 JSON·본문 JSON만 있으면 됩니다
3. 신청할 때 직접 정한 API 인증키(OC)를 `.env`의 `LAW_API_OC`에 넣습니다 (마이페이지 > API인증키관리에서 확인)
4. 가져올 법령은 `data/target_laws.json`에서 관리합니다

`LAW_DB_URL`이 없으면 `data/law_index.npz` 파일로 법령을 검색합니다 (작은 서버용, `scripts/export_law_index.py`로 생성). 파일도 없으면 법령 검색 없이 규칙 엔진만 동작합니다.

## OCR

1. 텍스트가 들어있는 PDF는 OCR 없이 바로 읽습니다.
2. APP이 기기 OCR 결과를 `ocrResult`로 보내면 그대로 씁니다.
3. 서버 OCR(PaddleOCR)은 선택입니다: `pip install -r requirements-ocr.txt` 또는 `docker build --build-arg WITH_OCR=1`.
   설치하지 않으면 `ocrResult` 없는 사진은 422(`TEXT_NOT_RECOGNIZED`)가 됩니다.

## 개인정보

- 외부 LLM에는 생년월일 대신 계산된 나이만 보냅니다.
- `consents.overseasTransfer`가 false면 프로필을 외부 LLM에 보내지 않습니다 (규칙 엔진 점검만).
- OpenAI 호출은 `store=False`로 보냅니다.
