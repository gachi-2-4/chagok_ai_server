from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # BE → AI
    ai_api_key: str = "change-me"

    # AI → BE
    be_base_url: str = "http://localhost:8080"
    be_internal_key: str = "change-me"
    be_timeout_seconds: float = 10.0

    # OpenAI
    openai_api_key: str = ""
    openai_model_main: str = "gpt-6-luna"
    openai_model_light: str = "gpt-6-luna"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_reasoning_effort: str = ""

    # 법령 검색: LAW_DB_URL 이 있으면 DB, 없으면 LAW_INDEX_PATH 파일 (작은 서버용)
    law_db_url: str = ""
    law_index_path: str = "data/law_index.npz"
    law_api_oc: str = ""

    # 제한값
    max_file_mb: int = 20
    max_pdf_pages: int = 20
    analysis_timeout_seconds: float = 90.0
    chat_timeout_seconds: float = 30.0
    compare_timeout_seconds: float = 20.0
    chat_rate_per_minute: int = 10
    review_confidence: float = 0.7


@lru_cache
def get_settings() -> Settings:
    return Settings()
