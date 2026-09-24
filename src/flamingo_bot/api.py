"""FastAPI transport for health, readiness, and cited streaming chat."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from os import PathLike

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from flamingo_bot.config import Settings, get_settings, load_model_config
from flamingo_bot.errors import FlamingoBotError
from flamingo_bot.models import ConversationTurn
from flamingo_bot.ports import (
    AnswerGenerator,
    ClosableProvider,
    Embedder,
    QueryCondenser,
    RequestQuota,
    VectorStore,
)
from flamingo_bot.providers.azure import AzureModelProvider
from flamingo_bot.providers.firestore import FirestoreRequestQuota, FirestoreVectorStore
from flamingo_bot.quota import InMemoryRequestQuota, QuotaWindow
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
    request_quota: RequestQuota | None = None
    closables: tuple[ClosableProvider, ...] = ()


class OriginGuardMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: tuple[str, ...],
        require_origin_paths: tuple[str, ...] = (),
    ) -> None:
        self.app = app
        self.allowed_origins = set(allowed_origins)
        self.require_origin_paths = set(require_origin_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            origin = Headers(scope=scope).get("origin")
        else:
            origin = None
        if not origin and scope.get("path") in self.require_origin_paths:
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"error": "origin_required"},
            )
            await response(scope, receive, send)
            return
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


class WidgetStaticFiles(StaticFiles):
    """Serves the widget with an explicit freshness policy.

    Third-party pages hardcode ``/widget/flamingo-chat.js``, so the bundle cannot
    carry a content hash in its name the way an app build would. Starlette sends
    an ETag but no ``Cache-Control``, and a browser given no policy falls back to
    heuristic caching, which lets an embedding site keep serving a superseded
    widget long after a release. Since the name is stable, freshness has to come
    from the header instead.

    The script and its styles revalidate on every load, which costs one
    conditional request and usually returns 304 with no body. The avatar media is
    large, changes only when the assets are replaced, and is cached hard.
    """

    #: Immutable enough to cache for a year; replacing these means a new release.
    LONG_LIVED_SUFFIXES = (".mp4", ".webm", ".webp", ".woff2", ".png", ".jpg", ".svg")

    def file_response(
        self,
        full_path: str | PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if str(full_path).endswith(self.LONG_LIVED_SUFFIXES):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def _client_address(request: Request, trusted_proxy_hops: int) -> str:
    """Resolve the caller's address from the trusted tail of ``X-Forwarded-For``.

    Only the entries Google's front end appends can be trusted; anything a client
    sends arrives as a prefix in front of them and is attacker-controlled. Counting
    back from the end is therefore the only safe direction, and how far back
    depends on the ingress in use, so ``trusted_proxy_hops`` names it explicitly
    rather than guessing. Reached directly on its ``run.app`` URL, Cloud Run appends
    exactly one entry, the caller, so the default of zero trailing proxy hops puts
    the caller last.

    A header shorter than the configured topology means the deployment does not
    match its configuration. That falls back to the socket peer, which over-groups
    callers rather than handing out a fresh bucket per forged header.
    """
    forwarded = [
        item.strip()
        for item in request.headers.get("x-forwarded-for", "").split(",")
        if item.strip()
    ]
    index = len(forwarded) - 1 - trusted_proxy_hops
    if index >= 0:
        return forwarded[index]
    if forwarded:
        logger.warning(
            "forwarded_for_shorter_than_trusted_hops entries=%d hops=%d",
            len(forwarded),
            trusted_proxy_hops,
        )
    return request.client.host if request.client else "unknown"


def _client_key(request: Request, trusted_proxy_hops: int = 0) -> str:
    return hashlib.sha256(_client_address(request, trusted_proxy_hops).encode()).hexdigest()


def _safety_identifier(request: Request, settings: Settings, client_key: str) -> str | None:
    if settings.flamingo_safety_salt is None:
        return None
    salt = settings.flamingo_safety_salt.get_secret_value()
    if not salt:
        return None
    return hashlib.sha256(f"{salt}:{client_key}".encode()).hexdigest()


def create_app(
    *,
    settings: Settings | None = None,
    services: ServiceBundle | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()

    client_quota_window = QuotaWindow(
        limit=runtime_settings.flamingo_client_quota_requests,
        window_seconds=runtime_settings.flamingo_client_quota_window_seconds,
    )
    daily_quota_window = QuotaWindow(
        limit=runtime_settings.flamingo_daily_quota_requests,
        window_seconds=runtime_settings.flamingo_daily_quota_window_seconds,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if services is None:
            model_config = load_model_config(runtime_settings.model_config_path)
            model_provider = AzureModelProvider(runtime_settings, model_config)
            vector_store = FirestoreVectorStore(runtime_settings)
            # A second client, against a second database. Firestore grants write
            # access per database, so this separation is what keeps the corpus
            # read-only to the serving identity while its counters stay writable.
            request_quota = FirestoreRequestQuota(
                runtime_settings,
                client_quota_window,
                daily_quota_window,
            )
            bundle = ServiceBundle(
                embedder=model_provider,
                vector_store=vector_store,
                answer_generator=model_provider,
                query_condenser=model_provider,
                request_quota=request_quota,
                closables=(model_provider, vector_store, request_quota),
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
        # A bundle assembled by a caller that does not supply a quota gets a
        # process-local one with the same limits, which is correct for a single
        # test or development process and never used where instances scale out.
        app.state.request_quota = bundle.request_quota or InMemoryRequestQuota(
            client_quota_window, daily_quota_window
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
        require_origin_paths=(
            ("/v1/chat",) if runtime_settings.flamingo_environment == "production" else ()
        ),
    )
    if runtime_settings.flamingo_widget_dir.is_dir():
        application.mount(
            "/widget",
            WidgetStaticFiles(directory=runtime_settings.flamingo_widget_dir),
            name="widget",
        )

    standalone_page = runtime_settings.flamingo_widget_dir / "index.html"
    standalone_html = (
        standalone_page.read_text(encoding="utf-8") if standalone_page.is_file() else None
    )

    @application.get("/", include_in_schema=False)
    async def standalone_chat() -> HTMLResponse:
        if standalone_html is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Standalone chat is unavailable because the widget has not been built.",
            )
        return HTMLResponse(standalone_html, headers={"Cache-Control": "no-cache"})

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
        client_key = _client_key(request, runtime_settings.flamingo_trusted_proxy_hops)
        # The in-process limiter is the cheap burst shield. It sheds a flood before
        # it can cost a Firestore transaction, and the durable quotas behind it are
        # what actually bound the day.
        if not await limiter.allow(client_key):
            raise HTTPException(status_code=429, detail="rate_limit_exceeded")

        quota: RequestQuota = request.app.state.request_quota
        try:
            decision = await quota.consume(client_key)
        except FlamingoBotError as exc:
            # Fail closed. The quota exists to bound spend, so an unreadable
            # counter must not become an unmetered request. Retrieval depends on
            # the same Firestore database anyway, so this costs no availability
            # the request had to begin with.
            logger.warning("quota_unavailable error=%s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="quota_unavailable") from exc
        if not decision.allowed:
            logger.info("quota_exhausted scope=%s", decision.scope)
            raise HTTPException(
                status_code=429,
                detail=f"{decision.scope}_quota_exhausted",
                headers={"Retry-After": str(decision.retry_after_seconds)},
            )

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
                    safety_identifier=_safety_identifier(request, runtime_settings, client_key),
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
