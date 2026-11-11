@echo off
title NotebookLM Batch Studio
cd /d "%~dp0"

echo ========================================================
echo   Starting NotebookLM Batch Studio...
echo   Opening http://127.0.0.1:8765 in your browser...
echo ========================================================
echo.

:: Open browser after 1 second
start "" powershell -WindowStyle Hidden -Command "Start-Sleep -Seconds 1; Start-Process http://127.0.0.1:8765"

:: Launch Batch Studio web server
.\.venv\Scripts\python.exe -m notebooklm.batch --port 8765
