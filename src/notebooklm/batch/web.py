"""Loopback-only browser adapter for the persistent Studio batch runner."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from notebooklm.client import NotebookLMClient

from .core import Engine, Store, schema, validate_batch
from .exporter import build_zip_archive, compile_combined_pdf, download_artifact_file

# Opt into headless background re-auth by default for local batch studio
os.environ.setdefault("NOTEBOOKLM_HEADLESS_REAUTH", "1")

logger = logging.getLogger(__name__)
MAX_BODY_BYTES = 1024 * 1024


def default_client_factory():
    """Avoid hidden transport retries of an ambiguously accepted creation."""
    return NotebookLMClient.from_storage(
        rate_limit_max_retries=0, server_error_max_retries=0, allow_headless=True
    )


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

    @app.get("/api/notebooks")
    async def list_notebooks():
        try:
            async with client_factory() as client:
                items = await client.notebooks.list()
                return [{"id": nb.id, "title": nb.title} for nb in items]
        except Exception as exc:
            logger.warning("Failed to list notebooks for UI: %s", exc)
            return JSONResponse(
                {
                    "error": "auth_required",
                    "detail": "Google authentication expired. Please re-authenticate.",
                },
                status_code=401,
            )

    @app.post("/api/auth/login")
    async def auth_login():
        from notebooklm._browser.headless_reauth import (
            HeadlessReauthStatus,
            attempt_headless_reauth,
        )
        from notebooklm.cli.session_cmd import _run_playwright_login
        from notebooklm.paths import get_browser_profile_dir, get_storage_path

        storage_path = get_storage_path()
        browser_profile = get_browser_profile_dir()

        # Step 1: Attempt silent headless re-auth first
        try:
            result = await asyncio.to_thread(
                attempt_headless_reauth,
                storage_path=storage_path,
                allow_headless=True,
                browser_profile=browser_profile,
            )
            if result.status == HeadlessReauthStatus.SUCCESS:
                return {"status": "ok", "message": "Google authentication refreshed headlessly."}
        except Exception as exc:
            logger.debug("Headless reauth attempt before interactive login yielded: %s", exc)

        # Step 2: If headless was insufficient, launch interactive browser login
        try:
            await asyncio.to_thread(
                _run_playwright_login,
                browser="chromium",
                browser_profile=browser_profile,
                storage_path=storage_path,
                browser_timeout=300,
            )
            return {"status": "ok", "message": "Google authentication completed successfully."}
        except Exception as exc:
            logger.error("Interactive login failed: %s", exc)
            return JSONResponse(
                {"status": "error", "detail": f"Login failed: {exc}"},
                status_code=500,
            )

    @app.get("/api/jobs")
    async def jobs(hours: float = 24.0, all: bool = False):
        if all or hours <= 0:
            return store.list()
        return store.list(since_seconds=hours * 3600)

    @app.post("/api/download/pdf")
    async def download_combined_pdf_endpoint(request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        job_ids = payload.get("job_ids") if isinstance(payload, dict) else None
        title = payload.get("title", "NotebookLM Zusammenfassung") if isinstance(payload, dict) else "NotebookLM Zusammenfassung"
        all_jobs = store.list()
        if job_ids:
            selected = [j for j in all_jobs if j["id"] in job_ids]
        else:
            selected = store.list(since_seconds=24 * 3600)
        completed = [j for j in selected if j.get("status") == "complete" and j.get("artifact_id")]
        if not completed:
            raise HTTPException(404, "Keine fertigen Aufträge zum Erstellen der PDF vorhanden.")
        try:
            async with client_factory() as client:
                pdf_bytes = await compile_combined_pdf(client, completed, title=title)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="notebooklm_zusammenfassung.pdf"'},
            )
        except Exception as exc:
            logger.error("Failed to compile PDF: %s", exc)
            raise HTTPException(500, f"Fehler beim Erstellen der PDF: {exc}") from exc

    @app.get("/api/download/combined-pdf")
    async def download_combined_pdf_get(hours: float = 24.0):
        all_jobs = store.list(since_seconds=hours * 3600) if hours > 0 else store.list()
        completed = [j for j in all_jobs if j.get("status") == "complete" and j.get("artifact_id")]
        if not completed:
            raise HTTPException(404, "Keine fertigen Aufträge in diesem Zeitraum gefunden.")
        try:
            async with client_factory() as client:
                pdf_bytes = await compile_combined_pdf(client, completed)
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={"Content-Disposition": 'attachment; filename="notebooklm_zusammenfassung.pdf"'},
            )
        except Exception as exc:
            logger.error("Failed to compile PDF: %s", exc)
            raise HTTPException(500, f"Fehler beim Erstellen der PDF: {exc}") from exc

    @app.post("/api/download/zip")
    async def download_zip_endpoint(request: Request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        job_ids = payload.get("job_ids") if isinstance(payload, dict) else None
        all_jobs = store.list()
        if job_ids:
            selected = [j for j in all_jobs if j["id"] in job_ids]
        else:
            selected = store.list(since_seconds=24 * 3600)
        completed = [j for j in selected if j.get("status") == "complete" and j.get("artifact_id")]
        if not completed:
            raise HTTPException(404, "Keine fertigen Aufträge zum Herunterladen vorhanden.")
        try:
            async with client_factory() as client:
                zip_bytes = await build_zip_archive(client, completed)
            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="notebooklm_auftraege.zip"'},
            )
        except Exception as exc:
            logger.error("Failed to build ZIP: %s", exc)
            raise HTTPException(500, f"Fehler beim Erstellen des ZIP-Archivs: {exc}") from exc

    @app.get("/api/jobs/{job_id}/download")
    async def download_single_job(job_id: str):
        row = next((r for r in store.list() if r["id"] == job_id), None)
        if not row:
            raise HTTPException(404, f"Job {job_id} nicht gefunden.")
        if row.get("status") != "complete" or not row.get("artifact_id"):
            raise HTTPException(400, "Dieser Auftrag ist noch nicht abgeschlossen.")
        job_info = row.get("job", {})
        notebook_id = job_info.get("notebook_id")
        artifact_id = row.get("artifact_id")
        job_type = job_info.get("type", "report")
        title = job_info.get("title", job_id)
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_dir:
                async with client_factory() as client:
                    file_path = await download_artifact_file(
                        client, notebook_id, artifact_id, job_type, title, Path(tmp_dir)
                    )
                    content = file_path.read_bytes()
                    filename = file_path.name
            ext = Path(filename).suffix.lower()
            media_type = "application/octet-stream"
            if ext == ".pdf":
                media_type = "application/pdf"
            elif ext in (".md", ".txt"):
                media_type = "text/markdown; charset=utf-8"
            elif ext == ".csv":
                media_type = "text/csv; charset=utf-8"
            elif ext == ".json":
                media_type = "application/json"
            elif ext == ".png":
                media_type = "image/png"
            elif ext in (".jpg", ".jpeg"):
                media_type = "image/jpeg"
            elif ext == ".mp3":
                media_type = "audio/mpeg"
            elif ext == ".mp4":
                media_type = "video/mp4"

            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as exc:
            logger.error("Failed to download job %s: %s", job_id, exc)
            raise HTTPException(500, f"Fehler beim Herunterladen: {exc}") from exc

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

    @app.post("/api/jobs/{job_id}/delete")
    async def delete_job(job_id: str):
        store.delete(job_id)
        return {"ok": True}

    return app
