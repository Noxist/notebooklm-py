"""Durable batch application using NotebookLM's public client operations."""

from __future__ import annotations

import asyncio
import builtins
import json
import logging
import math
import sqlite3
import time
from enum import Enum
from pathlib import Path
from typing import Any

from filelock import FileLock

from notebooklm import types

LOG = logging.getLogger(__name__)

# Registry values describe public method arguments; adding a Studio type is additive.
REGISTRY: dict[str, tuple[str, str | None, dict[str, type]]] = {
    "audio": (
        "generate_audio",
        "audio_format",
        {"audio_format": types.AudioFormat, "audio_length": types.AudioLength},
    ),
    "video": (
        "generate_video",
        "video_format",
        {"video_format": types.VideoFormat, "video_style": types.VideoStyle, "style_prompt": str},
    ),
    "cinematic_video": ("generate_cinematic_video", None, {}),
    "report": (
        "generate_report",
        "report_format",
        {"report_format": types.ReportFormat, "custom_prompt": str, "extra_instructions": str},
    ),
    "slide_deck": (
        "generate_slide_deck",
        "slide_format",
        {"slide_format": types.SlideDeckFormat, "slide_length": types.SlideDeckLength},
    ),
    "infographic": (
        "generate_infographic",
        None,
        {
            "orientation": types.InfographicOrientation,
            "detail_level": types.InfographicDetail,
            "style": types.InfographicStyle,
        },
    ),
    "quiz": (
        "generate_quiz",
        None,
        {
            "quantity": types.QuizQuantity,
            "difficulty": types.QuizDifficulty,
            "question_types": list,
            "include_images": bool,
        },
    ),
    "flashcards": (
        "generate_flashcards",
        None,
        {
            "quantity": types.QuizQuantity,
            "difficulty": types.QuizDifficulty,
            "include_images": bool,
        },
    ),
    "data_table": ("generate_data_table", None, {}),
    "mind_map_interactive": ("generate", None, {}),
    "mind_map_note": ("generate", None, {}),
}
NO_LATER = {"cinematic_video", "data_table", "mind_map_interactive", "mind_map_note"}
COMMON = {
    "id",
    "type",
    "notebook_id",
    "title",
    "prompt",
    "language",
    "format",
    "when",
    "source_ids",
    "options",
}


def _convert(value: Any, cls: type) -> Any:
    if isinstance(cls, type) and issubclass(cls, Enum):
        if isinstance(value, str) and value.upper() in cls.__members__:
            return cls[value.upper()]
        if isinstance(value, bool):
            raise ValueError("boolean is not an enum value")
        return cls(value)
    if cls is list:
        if not isinstance(value, list) or not value:
            raise ValueError("question_types must be a non-empty list")
        return [_convert(v, types.QuizQuestionType) for v in value]
    if type(value) is not cls:
        raise ValueError(f"expected {cls.__name__}")
    return value


def schema() -> dict:
    """Return available types, options and enum names for the editor."""
    return {
        name: {
            "later": name not in NO_LATER,
            "format": fmt,
            "options": {
                key: list(cls.__members__)
                if issubclass(cls, Enum)
                else (
                    list(types.QuizQuestionType.__members__)
                    if key == "question_types"
                    else cls.__name__
                )
                for key, cls in opts.items()
            },
        }
        for name, (_, fmt, opts) in REGISTRY.items()
    }


def call_args(job: dict) -> dict:
    """Convert persisted JSON into typed public API arguments."""
    kind = job["type"]
    _, fmt, spec = REGISTRY[kind]
    args = {k: _convert(v, spec[k]) for k, v in job.get("options", {}).items()}
    if job.get("format") is not None:
        if fmt is None:
            raise ValueError(f"format is unsupported for {kind}")
        if fmt in args:
            raise ValueError("format conflicts with options")
        args[fmt] = _convert(job["format"], spec[fmt])
    if job.get("prompt") is not None:
        key = "instructions"
        if kind == "report":
            key = (
                "custom_prompt"
                if args.get("report_format") == types.ReportFormat.CUSTOM
                else "extra_instructions"
            )
        if key in args:
            raise ValueError("prompt conflicts with options")
        args[key] = job["prompt"]
    if kind == "report":
        custom = args.get("report_format") == types.ReportFormat.CUSTOM
        ignored = "extra_instructions" if custom else "custom_prompt"
        if ignored in args:
            raise ValueError(f"{ignored} is ignored by this report format; use prompt")
    for key in ("source_ids", "language"):
        if key in job:
            args[key] = job[key]
    if kind not in NO_LATER:
        args["later"] = job.get("when", "now") == "later"
    if (
        kind == "video"
        and args.get("later")
        and args.get("video_format") == types.VideoFormat.CINEMATIC
    ):
        raise ValueError("cinematic video does not support Generate Later")
    if kind == "video":
        video_format, style = args.get("video_format"), args.get("video_style")
        style_prompt = args.get("style_prompt", "").strip()
        if video_format == types.VideoFormat.CINEMATIC and style_prompt:
            raise ValueError("cinematic video does not support style_prompt")
        if video_format == types.VideoFormat.SHORT and (
            style_prompt or style not in (None, types.VideoStyle.AUTO_SELECT)
        ):
            raise ValueError("short video has a fixed visual style")
        if style == types.VideoStyle.CUSTOM and not style_prompt:
            raise ValueError("CUSTOM video style requires style_prompt")
        if style_prompt and style != types.VideoStyle.CUSTOM:
            raise ValueError("style_prompt requires CUSTOM video_style")
    if kind.startswith("mind_map"):
        args["kind"] = (
            types.MindMapKind.INTERACTIVE
            if kind.endswith("interactive")
            else types.MindMapKind.NOTE_BACKED
        )
        args["wait"] = False
    return args


def validate_batch(payload: Any) -> builtins.list[dict]:
    """Validate the entire batch before persisting or invoking Google."""
    if isinstance(payload, dict):
        if set(payload) != {"jobs"}:
            raise ValueError("batch must contain only jobs")
        payload = payload["jobs"]
    if not isinstance(payload, list) or not payload or len(payload) > 1000:
        raise ValueError("jobs must contain 1 to 1000 jobs")
    seen = set()
    for job in payload:
        if not isinstance(job, dict) or set(job) - COMMON:
            raise ValueError("job contains unknown fields")
        for key in ("id", "type", "notebook_id", "title"):
            if not isinstance(job.get(key), str) or not job[key].strip():
                raise ValueError(f"{key} must be a non-empty string")
        if job["id"] in seen:
            raise ValueError("duplicate job id")
        seen.add(job["id"])
        if job["type"] not in REGISTRY:
            raise ValueError("unknown generation type")
        if job.get("when", "now") not in ("now", "later"):
            raise ValueError("when must be now or later; exact scheduling is unsupported")
        if job.get("when") == "later" and job["type"] in NO_LATER:
            raise ValueError(f"Generate Later unsupported for {job['type']}")
        for key in ("prompt", "language"):
            if key in job and not isinstance(job[key], str):
                raise ValueError(f"{key} must be a string")
        if job["type"] == "mind_map_interactive" and "language" in job:
            raise ValueError("web interactive mind maps do not support language selection")
        if "source_ids" in job and (
            not isinstance(job["source_ids"], list)
            or not job["source_ids"]
            or any(not isinstance(s, str) or not s.strip() for s in job["source_ids"])
        ):
            raise ValueError("source_ids must be a non-empty list of IDs")
        opts = job.get("options", {})
        if not isinstance(opts, dict) or set(opts) - set(REGISTRY[job["type"]][2]):
            raise ValueError("unknown type-specific option")
        call_args(job)
    return json.loads(json.dumps(payload))


class Store:
    """Single-owner SQLite journal. The lock prevents competing schedulers."""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(path) + ".lock")
        self.lock.acquire(timeout=0)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, job TEXT NOT NULL, status TEXT NOT NULL, artifact_id TEXT, error TEXT, renamed INTEGER NOT NULL DEFAULT 0)"
        )
        columns = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        if "remote_status" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN remote_status TEXT")
        self.db.execute(
            "UPDATE jobs SET status=CASE WHEN artifact_id IS NULL THEN 'uncertain' ELSE 'polling' END WHERE status='submitting'"
        )
        self.db.commit()

    def list(self) -> builtins.list[dict]:
        return [
            dict(row) | {"job": json.loads(row["job"])}
            for row in self.db.execute("SELECT * FROM jobs ORDER BY rowid")
        ]

    def update(self, id: str, **values: Any) -> None:
        if set(values) - {"status", "artifact_id", "error", "renamed", "remote_status"}:
            raise ValueError("invalid journal field")
        self.db.execute(
            "UPDATE jobs SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
            [*values.values(), id],
        )
        self.db.commit()
        if "status" in values:
            LOG.info("batch job %r state=%s", id, values["status"])

    def add(self, jobs: builtins.list[dict], dry_run: bool = False) -> builtins.list[dict]:
        jobs = validate_batch(jobs)
        existing = {r["id"]: r for r in self.list()}
        for j in jobs:
            if j["id"] in existing and existing[j["id"]]["job"] != j:
                raise ValueError("job id already exists with different content")
        if dry_run:
            return [{"id": j["id"], "job": j, "status": "dry_run"} for j in jobs]
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO jobs(id,job,status) VALUES (?,?,'queued')",
                [(j["id"], json.dumps(j, sort_keys=True)) for j in jobs],
            )
        return self.list()

    def retry(self, id: str) -> None:
        row = next((r for r in self.list() if r["id"] == id), None)
        if row is None:
            raise ValueError("unknown job")
        if row["status"] != "failed" or not row["artifact_id"]:
            raise ValueError("only jobs with a known artifact can retry; reconcile uncertain jobs")
        self.update(
            id,
            status="retry_queued" if row.get("remote_status") == "failed" else "polling",
            renamed=0,
            error=None,
        )

    def attach(self, id: str, artifact_id: str) -> None:
        row = next((r for r in self.list() if r["id"] == id), None)
        if (
            row is None
            or row["status"] != "uncertain"
            or not isinstance(artifact_id, str)
            or not artifact_id.strip()
        ):
            raise ValueError("attach requires an uncertain job and existing artifact ID")
        self.update(id, artifact_id=artifact_id, status="polling", error=None)

    def close(self) -> None:
        self.db.close()
        self.lock.release()


class Engine:
    """Bounded submission and resumable monitoring; never replays ambiguous creates."""

    def __init__(
        self, store: Store, client_factory: Any, concurrency: int = 2, interval: float = 2
    ):
        if (
            type(concurrency) is not int
            or not 1 <= concurrency <= 16
            or not math.isfinite(interval)
            or interval < 0
        ):
            raise ValueError("concurrency must be 1..16 and interval nonnegative")
        self.store, self.client_factory = store, client_factory
        self.semaphore = asyncio.Semaphore(concurrency)
        self.rate = asyncio.Lock()
        self.running = asyncio.Lock()
        self.interval = interval
        self.last = 0.0

    async def _pace(self) -> None:
        async with self.rate:
            await asyncio.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()

    async def run_once(self) -> None:
        async with self.running:
            rows = [
                r for r in self.store.list() if r["status"] in ("queued", "polling", "retry_queued")
            ]
            if rows:
                async with self.client_factory() as client:
                    await asyncio.gather(*(self._job(client, r) for r in rows))

    async def _job(self, client: Any, row: dict) -> None:
        async with self.semaphore:
            job, id = row["job"], row["id"]
            notebook = job["notebook_id"]
            artifact_id = row["artifact_id"]
            try:
                if row["status"] == "retry_queued":
                    # Persist before the mutation. A crash resumes polling the same ID.
                    self.store.update(id, status="polling", error=None)
                    await self._pace()
                    await client.artifacts.retry_failed(notebook, artifact_id)
                if not artifact_id:
                    await self._pace()
                    self.store.update(id, status="submitting")
                    api = (
                        client.mind_maps if job["type"].startswith("mind_map") else client.artifacts
                    )
                    result = await getattr(api, REGISTRY[job["type"]][0])(
                        notebook, **call_args(job)
                    )
                    artifact_id = getattr(result, "task_id", None) or getattr(result, "id", None)
                    if not artifact_id:
                        raise RuntimeError("generation returned no artifact ID")
                    self.store.update(id, status="polling", artifact_id=artifact_id)
                # Attempt the title immediately; retry after readiness if visibility lags.
                if not row["renamed"]:
                    try:
                        await self._rename(client, job, artifact_id)
                        self.store.update(id, renamed=1)
                        row["renamed"] = 1
                    except Exception:
                        LOG.info("batch rename deferred for job %s", id)
                if job["type"] == "mind_map_note":
                    complete = True
                else:
                    await self._pace()
                    status = await client.artifacts.poll_status(notebook, artifact_id)
                    self.store.update(id, remote_status=str(getattr(status, "status", "unknown")))
                    if status.is_failed or getattr(status, "is_removed", False):
                        self.store.update(
                            id,
                            status="failed",
                            error="Google generation failed; retry monitors the same artifact",
                        )
                        return
                    complete = status.is_complete
                if complete:
                    # Google may replace a title when an asynchronous job finishes.
                    await self._pace()
                    api = (
                        client.mind_maps if job["type"].startswith("mind_map") else client.artifacts
                    )
                    artifact = await api.get(notebook, artifact_id)
                    if artifact.id != artifact_id or artifact.title != job["title"]:
                        await self._rename(client, job, artifact_id)
                    self.store.update(id, renamed=1)
                self.store.update(id, status="complete" if complete else "polling", error=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.store.update(
                    id, status="failed" if artifact_id else "uncertain", error=type(exc).__name__
                )
                LOG.warning("batch job %s: %s", id, type(exc).__name__)

    async def _rename(self, client: Any, job: dict, artifact_id: str) -> None:
        await self._pace()
        if job["type"].startswith("mind_map"):
            kind = (
                types.MindMapKind.INTERACTIVE
                if job["type"].endswith("interactive")
                else types.MindMapKind.NOTE_BACKED
            )
            renamed = await client.mind_maps.rename(
                job["notebook_id"], artifact_id, job["title"], kind=kind
            )
        else:
            renamed = await client.artifacts.rename(job["notebook_id"], artifact_id, job["title"])
        if renamed is None or renamed.title != job["title"] or renamed.id != artifact_id:
            raise RuntimeError("rename verification failed")
