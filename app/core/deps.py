"""앱 전역 객체(클라이언트·서비스)를 app.state 에 두고 라우터에서 꺼내 씁니다. 테스트에서는 가짜로 바꿔 끼웁니다."""

from dataclasses import dataclass

from fastapi import Request

from app.clients.backend import BackendClient
from app.clients.llm import LLMClient
from app.core.config import Settings
from app.legal.repository import LawRepository
from app.services.chat import ChatService
from app.services.comparison import ComparisonService
from app.services.inquiry import InquiryService
from app.services.suggestions import SuggestionService
from app.services.summary_card import SummaryCardService


@dataclass
class Container:
    settings: Settings
    backend: BackendClient
    llm: LLMClient
    laws: LawRepository
    summary_card: SummaryCardService
    suggestions: SuggestionService
    chat: ChatService
    comparison: ComparisonService
    inquiry: InquiryService


def build_container(settings: Settings, backend: BackendClient | None = None, llm: LLMClient | None = None, laws: LawRepository | None = None) -> Container:
    backend = backend or BackendClient(settings)
    llm = llm or LLMClient(settings)
    laws = laws or LawRepository(settings.law_db_url, llm)
    return Container(
        settings=settings,
        backend=backend,
        llm=llm,
        laws=laws,
        summary_card=SummaryCardService(backend, llm),
        suggestions=SuggestionService(backend),
        chat=ChatService(backend, llm, laws, settings.chat_rate_per_minute),
        comparison=ComparisonService(backend, llm),
        inquiry=InquiryService(backend, llm),
    )


def get_container(request: Request) -> Container:
    return request.app.state.container
