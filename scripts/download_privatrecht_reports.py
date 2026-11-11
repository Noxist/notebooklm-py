import asyncio
import os
import re
import shutil
import zipfile
from pathlib import Path
from notebooklm.client import NotebookLMClient

NOTEBOOK_ID = "c365860d-8e74-4073-b48b-be4a0d5e6e1e"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "temp_privatrecht_reports"
ZIP_PATH = PROJECT_ROOT / "Privatrecht_Reports.zip"

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return cleaned or "Report"

async def main():
    print(f"Connecting to NotebookLM for notebook: {NOTEBOOK_ID}...")
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async with NotebookLMClient.from_storage() as client:
        artifacts = await client.artifacts.list(NOTEBOOK_ID)
        print(f"Found {len(artifacts)} total artifact(s) in Privatrecht notebook:")
        
        downloaded = []
        for i, art in enumerate(artifacts, 1):
            title = art.title or f"Artifact_{art.id}"
            safe_title = sanitize_filename(title)
            out_file = OUTPUT_DIR / f"{safe_title}.md"
            print(f"[{i}/{len(artifacts)}] Downloading '{title}' (ID: {art.id})...")
            try:
                # Try downloading report markdown
                result_path = await client.artifacts.download_report(NOTEBOOK_ID, str(out_file), artifact_id=art.id)
                print(f"   -> Saved to {result_path}")
                downloaded.append(out_file)
            except Exception as e:
                print(f"   -> Failed downloading as report ({e}), trying generic get...")
                try:
                    full_art = await client.artifacts.get(NOTEBOOK_ID, art.id)
                    content = getattr(full_art, "content", None) or getattr(full_art, "text", None) or str(full_art)
                    out_file.write_text(content, encoding="utf-8")
                    print(f"   -> Saved generic content ({len(content)} chars)")
                    downloaded.append(out_file)
                except Exception as e2:
                    print(f"   -> Could not fetch artifact {art.id}: {e2}")

        if not downloaded:
            print("No files were downloaded.")
            return

        print(f"\nCompressing {len(downloaded)} reports into {ZIP_PATH}...")
        with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file_path in downloaded:
                if file_path.exists():
                    zipf.write(file_path, arcname=file_path.name)

        print(f"\n[SUCCESS] Successfully created ZIP archive: {ZIP_PATH}")
        print(f"Archive size: {ZIP_PATH.stat().st_size:,} bytes")
        shutil.rmtree(OUTPUT_DIR, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(main())
