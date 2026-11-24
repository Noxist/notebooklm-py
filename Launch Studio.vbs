Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
WshShell.Environment("PROCESS")("NOTEBOOKLM_HEADLESS_REAUTH") = "1"
WshShell.Run "powershell -WindowStyle Hidden -ExecutionPolicy Bypass -Command ""if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) { Start-Process -WindowStyle Hidden -FilePath '.\.venv\Scripts\pythonw.exe' -ArgumentList '-m notebooklm.batch --port 8765' -WorkingDirectory $PWD }; for ($i=0; $i -lt 40; $i++) { if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { break }; Start-Sleep -Milliseconds 250 }; Start-Process 'http://127.0.0.1:8765'""", 0, False
