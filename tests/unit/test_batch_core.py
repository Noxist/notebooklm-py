"""Batch tests never contact Google."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from notebooklm.batch.core import REGISTRY, Engine, Store, call_args, validate_batch
from notebooklm.types import ReportFormat


def job(kind="audio", **extra):
    return dict(id="j", type=kind, notebook_id="nb", title="My title", **extra)


@pytest.mark.parametrize("kind", REGISTRY)
def test_every_type_validates(kind):
    assert validate_batch([job(kind)])[0]["type"] == kind


@pytest.mark.parametrize(
    "kind", ["cinematic_video", "data_table", "mind_map_interactive", "mind_map_note"]
)
def test_later_unsupported(kind):
    with pytest.raises(ValueError):
        validate_batch([job(kind, when="later")])


@pytest.mark.parametrize(
    "extra",
    [
        {"options": {"unknown": 1}},
        {"when": "2030-01-01"},
        {"source_ids": []},
        {"options": {"audio_length": True}},
    ],
)
def test_bad_options(extra):
    with pytest.raises(ValueError):
        validate_batch([job(**extra)])


def test_report_prompt_reaches_preset():
    assert call_args(job("report", format="STUDY_GUIDE", prompt="Focus")) == {
        "report_format": ReportFormat.STUDY_GUIDE,
        "extra_instructions": "Focus",
        "later": False,
    }


def test_store_idempotence_and_dry_run(tmp_path):
    s = Store(tmp_path / "jobs.db")
    assert s.add([job()], dry_run=True)[0]["status"] == "dry_run"
    assert not s.list()
    s.add([job()])
    assert len(s.add([job()])) == 1
    with pytest.raises(ValueError):
        s.add([job(prompt="different")])
    s.update("j", status="submitting")
    s.close()
    s = Store(tmp_path / "jobs.db")
    assert s.list()[0]["status"] == "uncertain"
    with pytest.raises(ValueError):
        s.retry("j")
    s.attach("j", "existing")
    assert s.list()[0]["artifact_id"] == "existing"
    s.close()


@pytest.mark.asyncio
async def test_engine_submit_rename_verify_resume(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.add([job()])
    api = SimpleNamespace(
        generate_audio=AsyncMock(return_value=SimpleNamespace(task_id="art")),
        get=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        rename=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        poll_status=AsyncMock(return_value=SimpleNamespace(is_complete=True, is_failed=False)),
    )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    engine = Engine(store, client, interval=0)
    await engine.run_once()
    await engine.run_once()
    assert store.list()[0]["status"] == "complete"
    api.generate_audio.assert_awaited_once()
    api.rename.assert_awaited_once_with("nb", "art", "My title")
    store.close()


@pytest.mark.asyncio
async def test_ambiguous_submit_never_replays(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.add([job()])
    api = SimpleNamespace(generate_audio=AsyncMock(side_effect=TimeoutError))

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    engine = Engine(store, client, interval=0)
    await engine.run_once()
    await engine.run_once()
    assert store.list()[0]["status"] == "uncertain"
    api.generate_audio.assert_awaited_once()
    store.close()


@pytest.mark.parametrize("kind", REGISTRY)
@pytest.mark.asyncio
async def test_every_type_dispatches_and_renames(tmp_path, kind):
    store = Store(tmp_path / "jobs.db")
    store.add([job(kind)])
    method = REGISTRY[kind][0]
    generate = AsyncMock(return_value=SimpleNamespace(task_id="art", id="art"))
    rename = AsyncMock(return_value=SimpleNamespace(id="art", title="My title"))
    api = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        rename=rename,
        poll_status=AsyncMock(
            return_value=SimpleNamespace(status="completed", is_complete=True, is_failed=False)
        ),
    )
    setattr(api, method, generate)

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api, mind_maps=api)

    await Engine(store, client, interval=0).run_once()
    assert store.list()[0]["status"] == "complete"
    generate.assert_awaited_once()
    rename.assert_awaited_once()
    store.close()


@pytest.mark.asyncio
async def test_retry_existing_failed_artifact(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.add([job()])
    store.update("j", artifact_id="existing", status="failed", remote_status="failed")
    store.retry("j")
    api = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id="existing", title="My title")),
        retry_failed=AsyncMock(),
        rename=AsyncMock(return_value=SimpleNamespace(id="existing", title="My title")),
        poll_status=AsyncMock(
            return_value=SimpleNamespace(status="completed", is_complete=True, is_failed=False)
        ),
    )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    await Engine(store, client, interval=0).run_once()
    api.retry_failed.assert_awaited_once_with("nb", "existing")
    assert store.list()[0]["status"] == "complete"
    store.close()


@pytest.mark.parametrize(
    "concurrency,interval", [(True, 1), (0, 1), (1, float("nan")), (1, float("inf")), (1, -1)]
)
def test_bad_runner_configuration(tmp_path, concurrency, interval):
    store = Store(tmp_path / "jobs.db")
    with pytest.raises(ValueError):
        Engine(store, None, concurrency, interval)
    store.close()


def test_registry_covers_public_options():
    import inspect

    from notebooklm.client import ArtifactsAPI

    common = {"self", "notebook_id", "source_ids", "language", "instructions", "later"}
    for kind, (method, _, options) in REGISTRY.items():
        if kind.startswith("mind_map"):
            continue
        assert set(inspect.signature(getattr(ArtifactsAPI, method)).parameters) - common == set(
            options
        )


@pytest.mark.asyncio
async def test_scheduled_queue_and_rename_retry_without_generation(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.add(
        [
            job(
                when="later",
                language="de",
                source_ids=["src"],
                options={"audio_format": "DEEP_DIVE"},
            )
        ]
    )
    api = SimpleNamespace(
        generate_audio=AsyncMock(return_value=SimpleNamespace(task_id="art")),
        get=AsyncMock(return_value=SimpleNamespace(id="art", title="Wrong title")),
        rename=AsyncMock(side_effect=RuntimeError),
        poll_status=AsyncMock(
            return_value=SimpleNamespace(status="scheduled", is_complete=False, is_failed=False)
        ),
    )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    engine = Engine(store, client, interval=0)
    await engine.run_once()
    row = store.list()[0]
    assert row["status"] == "polling" and row["remote_status"] == "scheduled"
    args = api.generate_audio.call_args.kwargs
    assert args["source_ids"] == ["src"] and args["language"] == "de" and args["later"] is True
    api.poll_status.return_value = SimpleNamespace(
        status="completed", is_complete=True, is_failed=False
    )
    await engine.run_once()
    assert store.list()[0]["status"] == "failed"
    store.retry("j")
    api.rename.side_effect = None
    api.rename.return_value = SimpleNamespace(id="art", title="My title")
    await engine.run_once()
    assert store.list()[0]["status"] == "complete"
    api.generate_audio.assert_awaited_once()
    store.close()


@pytest.mark.asyncio
async def test_concurrent_cycles_bounded_and_no_duplicate(tmp_path):
    import asyncio

    store = Store(tmp_path / "jobs.db")
    jobs = [dict(job(), id=f"j{i}") for i in range(8)]
    store.add(jobs)
    active, peak, calls = 0, 0, 0

    async def generate(*args, **kwargs):
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        return SimpleNamespace(task_id="art")

    api = SimpleNamespace(
        generate_audio=generate,
        get=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        rename=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        poll_status=AsyncMock(
            return_value=SimpleNamespace(status="completed", is_complete=True, is_failed=False)
        ),
    )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    engine = Engine(store, client, concurrency=2, interval=0)
    await asyncio.gather(engine.run_once(), engine.run_once())
    assert calls == 8 and peak == 2
    store.close()


@pytest.mark.asyncio
async def test_completion_corrects_overwritten_title(tmp_path):
    store = Store(tmp_path / "jobs.db")
    store.add([job()])
    store.update("j", artifact_id="art", status="polling", renamed=1)
    api = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(id="art", title="Google default")),
        rename=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        poll_status=AsyncMock(
            return_value=SimpleNamespace(status="completed", is_complete=True, is_failed=False)
        ),
    )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    await Engine(store, client, interval=0).run_once()
    api.rename.assert_awaited_once_with("nb", "art", "My title")
    assert store.list()[0]["status"] == "complete"
    store.close()


@pytest.mark.asyncio
async def test_crash_after_retry_acceptance_resumes_polling(tmp_path):
    import asyncio

    path = tmp_path / "jobs.db"
    store = Store(path)
    store.add([job()])
    store.update("j", artifact_id="art", status="failed", remote_status="failed", renamed=1)
    store.retry("j")
    assert store.list()[0]["renamed"] == 0
    api = SimpleNamespace(
        retry_failed=AsyncMock(side_effect=asyncio.CancelledError),
        get=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        rename=AsyncMock(return_value=SimpleNamespace(id="art", title="My title")),
        poll_status=AsyncMock(
            return_value=SimpleNamespace(status="completed", is_complete=True, is_failed=False)
        ),
    )

    @asynccontextmanager
    async def client():
        yield SimpleNamespace(artifacts=api)

    with pytest.raises(asyncio.CancelledError):
        await Engine(store, client, interval=0).run_once()
    store.close()
    store = Store(path)
    await Engine(store, client, interval=0).run_once()
    api.retry_failed.assert_awaited_once()
    assert store.list()[0]["status"] == "complete"
    store.close()


@pytest.mark.parametrize(
    "fmt,options",
    [
        ("CUSTOM", {"extra_instructions": "ignored"}),
        ("BRIEFING_DOC", {"custom_prompt": "ignored"}),
    ],
)
def test_ignored_report_options_are_explicitly_rejected(fmt, options):
    with pytest.raises(ValueError, match="ignored"):
        validate_batch([job("report", format=fmt, options=options)])
