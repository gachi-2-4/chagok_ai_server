"""OpenAI 호출. 모든 응답은 Structured Outputs(Pydantic 스키마)로 받습니다."""

import logging
from typing import TypeVar

from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import AppError

logger = logging.getLogger("chagok.llm")

T = TypeVar("T", bound=BaseModel)


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        if settings.openai_api_key:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=1)

    @property
    def main_model(self) -> str:
        return self.settings.openai_model_main

    @property
    def light_model(self) -> str:
        return self.settings.openai_model_light

    def _require(self):
        if self._client is None:
            raise AppError(500, "LLM_NOT_CONFIGURED", "OPENAI_API_KEY가 설정되지 않았습니다.")
        return self._client

    async def parse(
        self,
        schema: type[T],
        *,
        instructions: str,
        content: str,
        light: bool = False,
        max_output_tokens: int = 2000,
        cache_key: str | None = None,
    ) -> T:
        """instructions(고정 지시) + content(서류 등 입력)로 schema 형식의 결과를 받습니다.

        같은 서류를 여러 번 보내는 경우(채팅) content 앞부분을 고정해 두면 입력 캐시가 적용됩니다.
        """
        client = self._require()
        kwargs = {}
        if self.settings.openai_reasoning_effort:
            kwargs["reasoning"] = {"effort": self.settings.openai_reasoning_effort}
        if cache_key:
            kwargs["prompt_cache_key"] = cache_key
        response = await client.responses.parse(
            model=self.light_model if light else self.main_model,
            instructions=instructions,
            input=content,
            text_format=schema,
            max_output_tokens=max_output_tokens,
            store=False,
            **kwargs,
        )
        parsed = response.output_parsed
        if parsed is None:
            logger.warning("LLM returned no parsed output (status=%s)", getattr(response, "status", None))
            raise AppError(500, "AI_GENERATION_FAILED", "AI 응답을 해석하지 못했습니다.")
        return parsed

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._require()
        res = await client.embeddings.create(model=self.settings.openai_embedding_model, input=texts)
        return [d.embedding for d in res.data]

    @property
    def available(self) -> bool:
        return self._client is not None
