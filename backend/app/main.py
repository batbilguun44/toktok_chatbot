"""FastAPI entrypoint: `uvicorn app.main:app`."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, router
from app.config import get_settings
from app.container import build_container

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.container = await build_container(get_settings())
    try:
        yield
    finally:
        await app.state.container.aclose()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="TokTok Chatbot API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Admin-Key"],
    )
    app.include_router(router)
    app.include_router(admin)

    @app.get("/api/health")
    async def health():
        return {"ok": True}

    return app


app = create_app()
