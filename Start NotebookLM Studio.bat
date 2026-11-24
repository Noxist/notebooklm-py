@echo off
setlocal
cd /d "%~dp0"
set NOTEBOOKLM_HEADLESS_REAUTH=1

:: Start Batch Studio silently in background using pythonw (no terminal window)
netstat -ano | findstr ":8765.*LISTENING" >nul
if errorlevel 1 (
    start "" ".\.venv\Scripts\pythonw.exe" -m notebooklm.batch --port 8765
)

:: Open browser silently in background and exit command prompt immediately
start "" powershell -WindowStyle Hidden -Command "Start-Sleep -Milliseconds 800; Start-Process http://127.0.0.1:8765"
exit
