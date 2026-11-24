#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"
export NOTEBOOKLM_HEADLESS_REAUTH=1

# Start batch server in background if not already listening on port 8765
if ! curl -s http://127.0.0.1:8765/api/jobs > /dev/null 2>&1; then
    if [ -f "./.venv/Scripts/pythonw.exe" ]; then
        "./.venv/Scripts/pythonw.exe" -m notebooklm.batch --port 8765 &
    elif [ -f "./.venv/Scripts/python.exe" ]; then
        "./.venv/Scripts/python.exe" -m notebooklm.batch --port 8765 &
    elif [ -f "./.venv/bin/python" ]; then
        "./.venv/bin/python" -m notebooklm.batch --port 8765 &
    else
        python -m notebooklm.batch --port 8765 &
    fi
fi

# Wait for server to become ready (max 10s)
for i in {1..40}; do
    if curl -s http://127.0.0.1:8765/api/jobs > /dev/null 2>&1; then
        break
    fi
    sleep 0.25
done

# Open browser
if command -v start > /dev/null 2>&1; then
    start "http://127.0.0.1:8765"
elif command -v xdg-open > /dev/null 2>&1; then
    xdg-open "http://127.0.0.1:8765"
elif command -v open > /dev/null 2>&1; then
    open "http://127.0.0.1:8765"
else
    cmd.exe /c start "http://127.0.0.1:8765"
fi
