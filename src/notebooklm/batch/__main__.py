"""Start the local JSON batch application without changing upstream CLI commands."""

from __future__ import annotations

import argparse
import logging
import math
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Local NotebookLM Studio JSON batch runner")
    parser.add_argument(
        "--database", "--db", type=Path, default=Path.home() / ".notebooklm" / "batch.sqlite3"
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--interval", type=float, default=2, help="Minimum seconds between public API calls"
    )
    parser.add_argument("--poll-interval", type=float, default=10)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if (
        not 1 <= args.concurrency <= 16
        or not math.isfinite(args.interval)
        or not math.isfinite(args.poll_interval)
        or args.interval < 0
        or args.poll_interval <= 0
    ):
        parser.error("concurrency between 1 and 16, interval >= 0 and poll-interval > 0 required")
    try:
        import uvicorn

        from .web import create_app
    except ImportError:
        parser.error("Install notebooklm-py[server] to run the batch web interface")
    logging.basicConfig(level=logging.INFO)
    args.database.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(
        args.database,
        concurrency=args.concurrency,
        interval=args.interval,
        poll_interval=args.poll_interval,
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
