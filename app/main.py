import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.deps import build_container
from app.core.errors import install_error_handlers
from app.routers import chats, comparisons, documents

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not hasattr(app.state, "container"):
        app.state.container = build_container(get_settings())
    yield
    await app.state.container.backend.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="차곡 AI 서버", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(documents.router)
    app.include_router(chats.router)
    app.include_router(comparisons.router)

    @app.get("/health", tags=["HEALTH"])
    async def health():
        c = getattr(app.state, "container", None)
        return {"status": "ok", "llm": bool(c and c.llm.available), "lawDb": bool(c and c.laws.enabled)}

    return app


app = create_app()
