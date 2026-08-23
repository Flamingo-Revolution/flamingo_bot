"""FastAPI transport for health, readiness, and cited streaming chat."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from flamingo_bot.config import Settings, get_settings, load_model_config
from flamingo_bot.errors import FlamingoBotError
from flamingo_bot.models import ConversationTurn
from flamingo_bot.ports import (
    AnswerGenerator,
    ClosableProvider,
    Embedder,
    QueryCondenser,
    VectorStore,
)
from flamingo_bot.providers.azure import AzureModelProvider
from flamingo_bot.providers.firestore import FirestoreVectorStore
from flamingo_bot.rate_limit import RateLimiter
from flamingo_bot.retrieval import ChatService

logger = logging.getLogger("flamingo_bot.api")


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    #: Earlier turns replayed by the client. The service keeps no conversation
    #: state, so this is the only way a follow-up can be understood.
    history: list[ConversationTurn] = Field(default_factory=list, max_length=16)


@dataclass
class ServiceBundle:
    embedder: Embedder
    vector_store: VectorStore
    answer_generator: AnswerGenerator
    query_condenser: QueryCondenser | None = None
    closables: tuple[ClosableProvider, ...] = ()


class OriginGuardMiddleware:
    def __init__(self, app: ASGIApp, allowed_origins: tuple[str, ...]) -> None:
        self.app = app
        self.allowed_origins = set(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origin = Headers(scope=scope).get("origin")
        else:
            origin = None
        if origin and origin.rstrip("/") not in self.allowed_origins:
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"error": "origin_not_allowed"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _client_key(request: Request) -> str:
    forwarded = [
        item.strip()
        for item in request.headers.get("x-forwarded-for", "").split(",")
        if item.strip()
    ]
    host = forwarded[-1] if forwarded else (request.client.host if request.client else "unknown")
    return hashlib.sha256(host.encode()).hexdigest()


def _safety_identifier(request: Request, settings: Settings) -> str | None:
    if settings.flamingo_safety_salt is None:
        return None
    salt = settings.flamingo_safety_salt.get_secret_value()
    if not salt:
        return None
    return hashlib.sha256(f"{salt}:{_client_key(request)}".encode()).hexdigest()


def create_app(
    *,
    settings: Settings | None = None,
    services: ServiceBundle | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is None:
            model_config = load_model_config(runtime_settings.model_config_path)
            model_provider = AzureModelProvider(runtime_settings, model_config)
            vector_store = FirestoreVectorStore(runtime_settings)
            bundle = ServiceBundle(
                embedder=model_provider,
                vector_store=vector_store,
                answer_generator=model_provider,
                query_condenser=model_provider,
                closables=(model_provider, vector_store),
            )
        else:
            bundle = services
        app.state.services = bundle
        app.state.chat_service = ChatService(
            settings=runtime_settings,
            embedder=bundle.embedder,
            vector_store=bundle.vector_store,
            answer_generator=bundle.answer_generator,
            query_condenser=bundle.query_condenser,
        )
        app.state.chat_semaphore = asyncio.Semaphore(runtime_settings.flamingo_chat_concurrency)
        app.state.rate_limiter = RateLimiter(
            runtime_settings.flamingo_rate_limit_requests,
            runtime_settings.flamingo_rate_limit_window_seconds,
        )
        try:
            yield
        finally:
            for closable in reversed(bundle.closables):
                await closable.close()

    application = FastAPI(
        title="Flamingo Bot API",
        version="0.1.0",
        docs_url=None if runtime_settings.flamingo_environment == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    application.add_middleware(
        OriginGuardMiddleware,
        allowed_origins=runtime_settings.allowed_origins,
    )
    if runtime_settings.flamingo_widget_dir.is_dir():
        application.mount(
            "/widget",
            StaticFiles(directory=runtime_settings.flamingo_widget_dir),
            name="widget",
        )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "alive"}

    @application.get("/status")
    async def readiness(request: Request) -> JSONResponse:
        bundle: ServiceBundle = request.app.state.services
        try:
            provider_status = await bundle.vector_store.status()
        except FlamingoBotError as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"ready": False, "error": type(exc).__name__},
            )
        status_code = (
            status.HTTP_200_OK
            if provider_status.get("ready")
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return JSONResponse(status_code=status_code, content=jsonable_encoder(provider_status))

    @application.post("/v1/chat")
    async def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        limiter: RateLimiter = request.app.state.rate_limiter
        client_key = _client_key(request)
        if not await limiter.allow(client_key):
            raise HTTPException(status_code=429, detail="rate_limit_exceeded")

        semaphore: asyncio.Semaphore = request.app.state.chat_semaphore
        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
        except TimeoutError as exc:
            raise HTTPException(status_code=429, detail="server_busy") from exc

        request_id = uuid.uuid4().hex
        started = time.monotonic()

        async def events() -> AsyncIterator[str]:
            citation_count = 0
            try:
                yield _sse("meta", {"request_id": request_id})
                chat_service: ChatService = request.app.state.chat_service
                citations, answer = await chat_service.stream(
                    payload.question.strip(),
                    history=payload.history,
                    safety_identifier=_safety_identifier(request, runtime_settings),
                )
                citation_count = len(citations)
                for citation in citations:
                    yield _sse("citation", citation.model_dump())
                async for delta in answer:
                    yield _sse("delta", {"text": delta})
                yield _sse("done", {"request_id": request_id})
                logger.info(
                    "chat_completed request_id=%s duration_ms=%d citations=%d history_turns=%d",
                    request_id,
                    int((time.monotonic() - started) * 1000),
                    citation_count,
                    len(payload.history),
                )
            except asyncio.CancelledError:
                logger.info("chat_cancelled request_id=%s", request_id)
                raise
            except FlamingoBotError as exc:
                logger.warning(
                    "chat_failed request_id=%s error=%s",
                    request_id,
                    type(exc).__name__,
                )
                yield _sse(
                    "error",
                    {"request_id": request_id, "code": type(exc).__name__},
                )
            finally:
                semaphore.release()

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
                "X-Request-ID": request_id,
            },
        )

    return application


app = create_app()
