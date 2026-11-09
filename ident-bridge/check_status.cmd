@echo off
echo === files ===
if exist "C:\ident-bridge\run_hidden.vbs" (echo OK run_hidden.vbs) else (echo MISSING run_hidden.vbs)
if exist "C:\ident-bridge\run_forever.cmd" (echo OK run_forever.cmd) else (echo MISSING run_forever.cmd)
if exist "C:\ident-bridge\venv\Scripts\python.exe" (echo OK python.exe) else (echo MISSING python.exe)
if exist "C:\ident-bridge\app.py" (echo OK app.py) else (echo MISSING app.py)
echo.
echo === task ===
schtasks /Query /TN IdentBridge /FO LIST /V
echo.
echo === python ===
tasklist /FI "IMAGENAME eq python.exe"
echo.
echo === last log ===
powershell -NoProfile -Command "if (Test-Path 'C:\ident-bridge\bridge.log') { Get-Content 'C:\ident-bridge\bridge.log' -Tail 30 } else { 'no bridge.log' }"
pause
