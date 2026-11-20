"""가짜 BE·LLM·법령 DB로 API 전체를 테스트합니다 (네트워크·OpenAI 호출 없음)."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.deps import build_container
from app.core.errors import AppError
from app.main import create_app

API_KEY = "test-key"


class FakeBackend:
    def __init__(self):
        self.users: dict[int, dict] = {}
        self.documents: dict[int, dict] = {}
        self.messages: dict[int, list] = {}
        self.comparisons: dict[int, dict] = {}
        self.calls: list[str] = []

    async def aclose(self):
        pass

    async def get_user(self, user_id):
        self.calls.append(f"user:{user_id}")
        if user_id not in self.users:
            raise AppError(404, "USER_NOT_FOUND", "사용자를 찾을 수 없습니다.")
        return self.users[user_id]

    async def get_document(self, document_id, user_id):
        self.calls.append(f"document:{document_id}")
        doc = self.documents.get(document_id)
        if not doc or doc.get("_owner") != user_id:
            raise AppError(404, "DOCUMENT_NOT_FOUND", "BE에서 서류를 찾을 수 없습니다.")
        return doc

    async def get_chat_messages(self, chat_id, user_id, size=10, before_message_id=None):
        return {"chatId": chat_id, "messages": self.messages.get(chat_id, [])[-size:], "hasMore": False}

    async def get_comparison(self, comparison_id, user_id):
        if comparison_id not in self.comparisons:
            raise AppError(404, "COMPARISON_NOT_FOUND", "BE에서 비교 결과를 찾을 수 없습니다.")
        return self.comparisons[comparison_id]


class FakeLLM:
    """schema 클래스 이름 → 결과(객체 또는 content를 받는 함수)."""

    def __init__(self):
        self.responses: dict[str, object] = {}
        self.calls: list[tuple[str, str]] = []
        self.main_model = self.light_model = "fake"

    available = True

    async def parse(self, schema, *, instructions, content, light=False, max_output_tokens=2000, cache_key=None):
        self.calls.append((schema.__name__, content))
        result = self.responses.get(schema.__name__)
        if result is None:
            raise AssertionError(f"no fake response for {schema.__name__}")
        if callable(result) and not isinstance(result, schema):
            result = result(content)
        return schema.model_validate(result) if isinstance(result, dict) else result

    async def embed(self, texts):
        return [[0.0] * 1536 for _ in texts]


class FakeLaws:
    enabled = False

    async def search(self, query, tags, k=6):
        return []

    async def get_article(self, law_name, article):
        return None

    async def latest_sync_date(self):
        return None


@pytest.fixture
def settings():
    return Settings(ai_api_key=API_KEY, openai_api_key="", law_db_url="")


@pytest.fixture
def be():
    return FakeBackend()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def client(settings, be, llm):
    app = create_app()
    app.state.container = build_container(settings, backend=be, llm=llm, laws=FakeLaws())
    app.dependency_overrides[get_settings] = lambda: settings
    get_settings.cache_clear()
    import os

    os.environ["AI_API_KEY"] = API_KEY
    with TestClient(app) as c:
        c.headers.update({"X-API-Key": API_KEY})
        yield c
    get_settings.cache_clear()


def user(age_birth="2001-03-14", overseas=True, profile=True):
    return {
        "userId": 1024,
        "birthDate": age_birth,
        "onboardingCompleted": profile,
        "consentRequired": False,
        "profile": {
            "nationality": "KR",
            "region": {"sido": "서울특별시", "sigungu": "마포구"},
            "housingType": "MONTHLY_RENT",
            "welfareType": "NONE",
        }
        if profile
        else None,
        "consents": {"termsOfService": True, "privacyPolicy": True, "guardianConsent": False, "overseasTransfer": overseas, "marketing": False},
    }
