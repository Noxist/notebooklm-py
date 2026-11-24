"""Artifact download and PDF compilation services for NotebookLM Batch Studio.

Uses the official NotebookLM-Py client API to fetch generated artifacts
and compiles them into high-quality PDFs or structured ZIP archives.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt
from playwright.async_api import async_playwright
from pypdf import PdfWriter

from notebooklm.client import NotebookLMClient

logger = logging.getLogger(__name__)

HTML_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<style>
  @page {{
    size: A4;
    margin: 20mm 18mm 20mm 18mm;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #24292f;
    background: #ffffff;
    margin: 0;
    padding: 0;
  }}
  .doc-header {{
    border-bottom: 2px solid #0969da;
    padding-bottom: 12px;
    margin-bottom: 24px;
  }}
  .doc-title {{
    font-size: 20pt;
    font-weight: 700;
    color: #1f2328;
    margin: 0 0 6px 0;
    line-height: 1.25;
  }}
  .doc-meta {{
    font-size: 9pt;
    color: #656d76;
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
  }}
  .doc-badge {{
    background: #ddf4ff;
    color: #0969da;
    padding: 2px 8px;
    border-radius: 12px;
    font-weight: 600;
  }}
  h1, h2, h3, h4, h5, h6 {{
    color: #1f2328;
    margin-top: 1.6em;
    margin-bottom: 0.6em;
    line-height: 1.3;
    page-break-after: avoid;
  }}
  h1 {{ font-size: 16pt; border-bottom: 1px solid #d0d7de; padding-bottom: 6px; }}
  h2 {{ font-size: 13.5pt; border-bottom: 1px solid #eaeef2; padding-bottom: 4px; }}
  h3 {{ font-size: 12pt; }}
  p {{ margin: 0.8em 0; }}
  blockquote {{
    border-left: 4px solid #0969da;
    margin: 1.2em 0;
    padding: 0.6em 1.2em;
    background: #f6f8fa;
    color: #57606a;
    border-radius: 0 4px 4px 0;
  }}
  code {{
    font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
    font-size: 9.5pt;
    background: #f6f8fa;
    padding: 2px 6px;
    border-radius: 4px;
    border: 1px solid #d0d7de;
  }}
  pre {{
    background: #24292f;
    color: #f6f8fa;
    padding: 14px;
    border-radius: 6px;
    overflow-x: auto;
    font-size: 9.5pt;
    line-height: 1.45;
  }}
  pre code {{
    background: transparent;
    color: inherit;
    padding: 0;
    border: none;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1.4em 0;
    font-size: 9.5pt;
  }}
  th, td {{
    border: 1px solid #d0d7de;
    padding: 8px 12px;
    text-align: left;
    vertical-align: top;
  }}
  th {{
    background: #f6f8fa;
    font-weight: 600;
    color: #1f2328;
  }}
  tr:nth-child(even) {{
    background: #fbfcfd;
  }}
  img {{
    max-width: 100%;
    height: auto;
    border-radius: 4px;
    display: block;
    margin: 1rem auto;
  }}
  ul, ol {{
    margin: 0.8em 0;
    padding-left: 24px;
  }}
  li {{
    margin: 0.35em 0;
  }}
  hr {{
    border: none;
    border-top: 1px solid #d0d7de;
    margin: 2em 0;
  }}
</style>
</head>
<body>
  <div class="doc-header">
    <div class="doc-title">{title}</div>
    <div class="doc-meta">
      <span class="doc-badge">{type_label}</span>
      <span>Generiert: {timestamp}</span>
      <span>NotebookLM</span>
    </div>
  </div>
  <div class="doc-content">
    {content}
  </div>
</body>
</html>
"""


def sanitize_filename(name: str) -> str:
    """Sanitize title for safe filesystem path."""
    clean = re.sub(r'[\\/*?:"<>|\r\n\t]', "_", name).strip()
    return clean[:120] if clean else "document"


async def download_artifact_file(
    client: NotebookLMClient,
    notebook_id: str,
    artifact_id: str,
    job_type: str,
    title: str,
    dest_dir: Path,
) -> Path:
    """Download an artifact to dest_dir using the official NotebookLM client API."""
    safe_title = sanitize_filename(title)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if job_type == "report":
        dest = dest_dir / f"{safe_title}.md"
        await client.artifacts.download_report(notebook_id, str(dest), artifact_id=artifact_id)
        return dest

    if job_type == "slide_deck":
        dest = dest_dir / f"{safe_title}.pdf"
        await client.artifacts.download_slide_deck(
            notebook_id, str(dest), artifact_id=artifact_id, output_format="pdf"
        )
        return dest

    if job_type == "quiz":
        dest = dest_dir / f"{safe_title}.md"
        await client.artifacts.download_quiz(
            notebook_id, str(dest), artifact_id=artifact_id, output_format="markdown"
        )
        return dest

    if job_type == "flashcards":
        dest = dest_dir / f"{safe_title}.md"
        await client.artifacts.download_flashcards(
            notebook_id, str(dest), artifact_id=artifact_id, output_format="markdown"
        )
        return dest

    if job_type == "data_table":
        dest = dest_dir / f"{safe_title}.csv"
        await client.artifacts.download_data_table(notebook_id, str(dest), artifact_id=artifact_id)
        return dest

    if job_type.startswith("mind_map"):
        dest = dest_dir / f"{safe_title}.json"
        await client.artifacts.download_mind_map(notebook_id, str(dest), artifact_id=artifact_id)
        return dest

    if job_type == "infographic":
        dest = dest_dir / f"{safe_title}.png"
        await client.artifacts.download_infographic(notebook_id, str(dest), artifact_id=artifact_id)
        return dest

    if job_type == "audio":
        dest = dest_dir / f"{safe_title}.mp3"
        await client.artifacts.download_audio(notebook_id, str(dest), artifact_id=artifact_id)
        return dest

    if job_type in ("video", "cinematic_video"):
        dest = dest_dir / f"{safe_title}.mp4"
        await client.artifacts.download_video(notebook_id, str(dest), artifact_id=artifact_id)
        return dest

    # Fallback to report download
    dest = dest_dir / f"{safe_title}.md"
    await client.artifacts.download_report(notebook_id, str(dest), artifact_id=artifact_id)
    return dest


def _mind_map_to_html(data: Any) -> str:
    """Format mind map JSON structure into hierarchical HTML list."""
    if isinstance(data, dict):
        title = data.get("title") or data.get("text") or data.get("name") or "Mind Map"
        html = f"<h3>{title}</h3>"
        children = data.get("children") or data.get("nodes") or []
        if children and isinstance(children, list):
            html += "<ul>"
            for child in children:
                html += f"<li>{_mind_map_to_html(child)}</li>"
            html += "</ul>"
        return html
    if isinstance(data, list):
        html = "<ul>"
        for item in data:
            html += f"<li>{_mind_map_to_html(item)}</li>"
        html += "</ul>"
        return html
    return str(data)


def _csv_to_html(content: str) -> str:
    """Format CSV text into an HTML table."""
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    if not rows:
        return "<p><em>Leere Tabelle</em></p>"
    html = "<table>"
    if rows:
        html += "<thead><tr>"
        for col in rows[0]:
            html += f"<th>{col}</th>"
        html += "</tr></thead>"
    if len(rows) > 1:
        html += "<tbody>"
        for row in rows[1:]:
            html += "<tr>"
            for cell in row:
                html += f"<td>{cell}</td>"
            html += "</tr>"
        html += "</tbody>"
    html += "</table>"
    return html


async def render_file_to_pdf(file_path: Path, title: str, job_type: str, page: Any) -> bytes:
    """Convert a downloaded artifact file into PDF bytes."""
    suffix = file_path.suffix.lower()

    # Native PDF (e.g. from slide decks)
    if suffix == ".pdf":
        return file_path.read_bytes()

    timestamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    type_label = job_type.replace("_", " ").title()

    if suffix in (".md", ".txt"):
        text = file_path.read_text(encoding="utf-8", errors="replace")
        md = MarkdownIt("commonmark", {"html": True, "typographer": True})
        content_html = md.render(text)
    elif suffix == ".csv":
        text = file_path.read_text(encoding="utf-8", errors="replace")
        content_html = _csv_to_html(text)
    elif suffix == ".json":
        try:
            data = json.loads(file_path.read_text(encoding="utf-8", errors="replace"))
            content_html = _mind_map_to_html(data)
        except Exception:
            content_html = f"<pre><code>{file_path.read_text(encoding='utf-8', errors='replace')}</code></pre>"
    elif suffix in (".png", ".jpg", ".jpeg", ".webp"):
        b64 = base64.b64encode(file_path.read_bytes()).decode("ascii")
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        content_html = f'<div style="text-align:center"><img src="data:{mime};base64,{b64}" alt="{title}"/></div>'
    elif suffix in (".mp3", ".wav", ".mp4"):
        content_html = (
            f"<div style='padding:2rem;background:#f6f8fa;border-radius:6px;border:1px solid #d0d7de;margin-top:1rem'>"
            f"<h3>Medien-Ausgabe: {title}</h3>"
            f"<p>Diese Datei ({file_path.name}) ist eine Mediendatei (Audio/Video). "
            f"Sie kann separat als Einzeldatei über den Download-Button oder im ZIP-Archiv heruntergeladen werden.</p>"
            f"</div>"
        )
    else:
        content_html = f"<pre><code>{file_path.read_text(encoding='utf-8', errors='replace')}</code></pre>"

    full_html = HTML_PAGE_TEMPLATE.format(
        title=title,
        type_label=type_label,
        timestamp=timestamp,
        content=content_html,
    )

    await page.set_content(full_html, wait_until="load")
    return await page.pdf(
        format="A4",
        margin={"top": "18mm", "bottom": "18mm", "left": "18mm", "right": "18mm"},
        print_background=True,
    )


async def compile_combined_pdf(
    client: NotebookLMClient,
    jobs: list[dict],
    title: str = "NotebookLM Batch Zusammenfassung",
) -> bytes:
    """Download and compile specified jobs into a single unified PDF document."""
    # Filter only jobs that are completed and have an artifact_id
    completed_jobs = [
        j for j in jobs
        if j.get("status") == "complete"
        and j.get("artifact_id")
        and j.get("job", {}).get("include_in_pdf") is not False
    ]

    if not completed_jobs:
        raise ValueError("Keine fertigen Aufträge für die PDF-Erstellung vorhanden.")

    # Sort by pdf_order (or order or position or fallback to default index)
    def sort_key(item: dict) -> tuple[int, int]:
        job_opts = item.get("job", {})
        order = job_opts.get("pdf_order")
        if order is None:
            order = job_opts.get("order")
        if order is None:
            order = job_opts.get("position")
        if order is not None:
            try:
                return (0, int(order))
            except (ValueError, TypeError):
                pass
        return (1, int(item.get("rowid") or 999999))

    sorted_jobs = sorted(completed_jobs, key=sort_key)

    writer = PdfWriter()

    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            for item in sorted_jobs:
                job_info = item.get("job", {})
                notebook_id = job_info.get("notebook_id")
                artifact_id = item.get("artifact_id")
                job_type = job_info.get("type", "report")
                item_title = job_info.get("title") or item.get("id") or "Dokument"

                try:
                    file_path = await download_artifact_file(
                        client, notebook_id, artifact_id, job_type, item_title, tmp_dir
                    )
                    pdf_bytes = await render_file_to_pdf(file_path, item_title, job_type, page)
                    writer.append(io.BytesIO(pdf_bytes))
                    logger.info("Compiled %s (%s) into PDF", item.get("id"), item_title)
                except Exception as exc:
                    logger.error("Failed to render %s to PDF: %s", item.get("id"), exc)

            await browser.close()

    if not writer.pages:
        raise RuntimeError("Es konnte keine PDF-Seite generiert werden.")

    output_stream = io.BytesIO()
    writer.write(output_stream)
    return output_stream.getvalue()


async def build_zip_archive(
    client: NotebookLMClient,
    jobs: list[dict],
) -> bytes:
    """Download individual files for specified jobs and package them into a ZIP archive."""
    completed_jobs = [
        j for j in jobs
        if j.get("status") == "complete" and j.get("artifact_id")
    ]

    if not completed_jobs:
        raise ValueError("Keine fertigen Aufträge zum Herunterladen ausgewählt.")

    zip_stream = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        with zipfile.ZipFile(zip_stream, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, item in enumerate(completed_jobs, 1):
                job_info = item.get("job", {})
                notebook_id = job_info.get("notebook_id")
                artifact_id = item.get("artifact_id")
                job_type = job_info.get("type", "report")
                item_title = job_info.get("title") or item.get("id") or f"Dokument_{idx}"

                try:
                    file_path = await download_artifact_file(
                        client, notebook_id, artifact_id, job_type, item_title, tmp_dir
                    )
                    arcname = f"{idx:02d}_{file_path.name}"
                    zf.write(file_path, arcname=arcname)
                except Exception as exc:
                    logger.error("Failed to download %s for ZIP: %s", item.get("id"), exc)

    return zip_stream.getvalue()
