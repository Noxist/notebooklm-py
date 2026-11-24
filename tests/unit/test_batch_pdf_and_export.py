"""Tests for batch PDF compilation, 24-hour job filtering, and export services."""

import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from notebooklm.batch.core import Store, validate_batch
from notebooklm.batch.exporter import build_zip_archive, compile_combined_pdf
from notebooklm.batch.web import create_app


def test_validate_batch_with_pdf_fields():
    # 1. Top-level combine_pdf and pdf_order
    payload = {
        "combine_pdf": True,
        "pdf_title": "Semester Zusammenfassung",
        "jobs": [
            {
                "id": "job-1",
                "type": "report",
                "notebook_id": "book-1",
                "title": "Report 1",
                "prompt": "Prompt 1",
                "include_in_pdf": True,
                "pdf_order": 2,
            },
            {
                "id": "job-2",
                "type": "report",
                "notebook_id": "book-1",
                "title": "Report 2",
                "prompt": "Prompt 2",
                # no pdf_order -> defaults to index 2
            },
            {
                "id": "job-3",
                "type": "audio",
                "notebook_id": "book-1",
                "title": "Audio 1",
                "include_in_pdf": False,
            },
        ],
    }

    validated = validate_batch(payload)
    assert len(validated) == 3
    # job-1 has explicit order 2
    assert validated[0]["include_in_pdf"] is True
    assert validated[0]["pdf_order"] == 2
    # job-2 has defaulted order = 2 (its index) and inherited include_in_pdf=True
    assert validated[1]["include_in_pdf"] is True
    assert validated[1]["pdf_order"] == 2
    # job-3 explicitly excluded
    assert validated[2]["include_in_pdf"] is False


def test_validate_batch_nested_pdf_dict():
    payload = {
        "jobs": [
            {
                "id": "job-a",
                "type": "report",
                "notebook_id": "book-1",
                "title": "Report A",
                "prompt": "Test",
                "pdf": {"include": True, "order": 5},
            }
        ]
    }
    validated = validate_batch(payload)
    assert validated[0]["include_in_pdf"] is True
    assert validated[0]["pdf_order"] == 5


def test_store_created_at_and_24h_filtering(tmp_path):
    store = Store(tmp_path / "test.sqlite3")

    now = time.time()
    jobs_payload = [
        {"id": "recent", "type": "report", "notebook_id": "book-1", "title": "Recent Job", "prompt": "P1"},
        {"id": "older", "type": "report", "notebook_id": "book-1", "title": "Older Job", "prompt": "P2"},
    ]
    store.add(jobs_payload)

    # Manually backdate "older" to 2 days ago
    store.update("older", created_at=now - (48 * 3600))

    # All jobs
    all_jobs = store.list()
    assert len(all_jobs) == 2

    # Filter 24 hours: should only return "recent"
    last_24h = store.list(since_seconds=24 * 3600)
    assert len(last_24h) == 1
    assert last_24h[0]["id"] == "recent"

    store.close()


@pytest.mark.asyncio
async def test_pdf_compilation_and_zip(tmp_path):
    # Create fake files to simulate client downloads
    doc1 = tmp_path / "report1.md"
    doc1.write_text("# Kapitel 1\n\nRechtliche Grundlagen", encoding="utf-8")

    doc2 = tmp_path / "data.csv"
    doc2.write_text("Thema,Punkte\nBGB,100\nOR,200", encoding="utf-8")

    fake_client = SimpleNamespace(
        artifacts=SimpleNamespace(
            download_report=AsyncMock(side_effect=lambda nb, path, **kw: Path(path).write_text("# Kapitel 1\n\nRechtliche Grundlagen", encoding="utf-8")),
            download_data_table=AsyncMock(side_effect=lambda nb, path, **kw: Path(path).write_text("Thema,Punkte\nBGB,100\nOR,200", encoding="utf-8")),
        )
    )

    jobs = [
        {
            "id": "j1",
            "status": "complete",
            "artifact_id": "art-1",
            "job": {
                "notebook_id": "nb-1",
                "type": "report",
                "title": "Bericht 1",
                "include_in_pdf": True,
                "pdf_order": 1,
            },
        },
        {
            "id": "j2",
            "status": "complete",
            "artifact_id": "art-2",
            "job": {
                "notebook_id": "nb-1",
                "type": "data_table",
                "title": "Tabelle 2",
                "include_in_pdf": True,
                "pdf_order": 2,
            },
        },
    ]

    # Test PDF Compilation
    pdf_bytes = await compile_combined_pdf(fake_client, jobs, title="Test Dokument")
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")

    # Test ZIP Archive
    zip_bytes = await build_zip_archive(fake_client, jobs)
    assert isinstance(zip_bytes, bytes)
    assert len(zip_bytes) > 200
    assert zip_bytes.startswith(b"PK")


def test_web_download_endpoints(tmp_path):
    fake_client = SimpleNamespace(
        artifacts=SimpleNamespace(
            download_report=AsyncMock(side_effect=lambda nb, path, **kw: Path(path).write_text("# Test\nInhalt", encoding="utf-8")),
        )
    )

    @asynccontextmanager
    async def factory():
        yield fake_client

    app = create_app(tmp_path / "web_test.sqlite3", client_factory=factory, run_workers=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        # Submit job
        res = client.post(
            "/api/submit",
            json={"jobs": [{"id": "doc-1", "type": "report", "notebook_id": "book", "title": "Test Dok", "prompt": "P"}]},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert res.status_code == 200

        # Mark complete in store
        app.state.store.update("doc-1", status="complete", artifact_id="art-xyz")

        # Test GET /api/jobs (defaults to 24h)
        jobs_24h = client.get("/api/jobs").json()
        assert len(jobs_24h) == 1

        # Test POST /api/download/pdf
        pdf_resp = client.post(
            "/api/download/pdf",
            json={"job_ids": ["doc-1"]},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"] == "application/pdf"
        assert pdf_resp.content.startswith(b"%PDF")

        # Test POST /api/download/zip
        zip_resp = client.post(
            "/api/download/zip",
            json={"job_ids": ["doc-1"]},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        assert zip_resp.status_code == 200
        assert zip_resp.headers["content-type"] == "application/zip"
        assert zip_resp.content.startswith(b"PK")

        # Test single download GET /api/jobs/doc-1/download
        single_resp = client.get("/api/jobs/doc-1/download")
        assert single_resp.status_code == 200
        assert "text/markdown" in single_resp.headers["content-type"]
        assert b"Inhalt" in single_resp.content
