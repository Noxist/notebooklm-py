"""Loopback-only browser adapter for the persistent Studio batch runner."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from notebooklm.client import NotebookLMClient

from .core import Engine, Store, schema, validate_batch

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024


def default_client_factory():
    """Avoid hidden transport retries of an ambiguously accepted creation."""
    return NotebookLMClient.from_storage(rate_limit_max_retries=0, server_error_max_retries=0)


def create_app(
    database: str | Path,
    *,
    concurrency: int = 2,
    interval: float = 2,
    poll_interval: float = 10,
    client_factory: Callable = default_client_factory,
    run_workers: bool = True,
) -> FastAPI:
    """Create a local app. Validation/dry runs never open a Google client."""
    if (
        type(concurrency) is not int
        or not 1 <= concurrency <= 16
        or not math.isfinite(interval)
        or not math.isfinite(poll_interval)
        or interval < 0
        or poll_interval <= 0
    ):
        raise ValueError(
            "concurrency between 1 and 16, interval >= 0 and poll_interval > 0 required"
        )
    store: Store
    engine: Engine

    async def worker() -> None:
        while True:
            try:
                if any(
                    row["status"] in {"queued", "polling", "retry_queued"} for row in store.list()
                ):
                    await engine.run_once()
            except Exception as exc:
                # User prompts and upstream exception strings may contain private data.
                logger.warning("Batch worker iteration failed (%s)", type(exc).__name__)
            await asyncio.sleep(poll_interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal store, engine
        store = Store(database)
        engine = Engine(store, client_factory, concurrency=concurrency, interval=interval)
        app.state.store = store
        task = asyncio.create_task(worker()) if run_workers else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.url.hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "Loopback Host required"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if not origin or origin != expected or urlsplit(origin).scheme != "http":
                return JSONResponse({"detail": "Same-origin request required"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "application/json required"}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    return JSONResponse({"detail": "JSON exceeds 1 MiB"}, status_code=413)
            # Cache for FastAPI's JSON parser after inspecting streamed bytes.
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

    @app.get("/app.js")
    async def javascript():
        from fastapi.responses import Response

        return Response(
            Path(__file__).with_name("app.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
        )

    @app.get("/api/schema")
    async def capabilities():
        return schema()

    @app.get("/api/jobs")
    async def jobs():
        return store.list()

    @app.post("/api/validate")
    async def validate(request: Request):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON") from exc
        return {"jobs": validate_batch(payload)}

    @app.post("/api/submit")
    async def submit(request: Request):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON") from exc
        return store.add(validate_batch(payload))

    @app.post("/api/dry-run")
    async def dry_run(request: Request):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON") from exc
        return {"dry_run": True, "jobs": validate_batch(payload)}

    @app.post("/api/jobs/{job_id}/retry")
    async def retry(job_id: str):
        store.retry(job_id)
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/attach")
    async def attach(job_id: str, request: Request):
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(422, "Invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("artifact_id"), str):
            raise HTTPException(422, "artifact_id string required")
        artifact_id = payload["artifact_id"].strip()
        if not artifact_id:
            raise HTTPException(422, "artifact_id must not be empty")
        store.attach(job_id, artifact_id)
        return {"ok": True}

    return app
