@echo off
cd /d "C:\ident-bridge"

if not exist "C:\ident-bridge\venv\Scripts\python.exe" (
  echo [%date% %time%] ERROR: python.exe not found >> "C:\ident-bridge\bridge.log"
  ping -n 6 127.0.0.1 >nul
  goto loop
)
if not exist "C:\ident-bridge\app.py" (
  echo [%date% %time%] ERROR: app.py not found >> "C:\ident-bridge\bridge.log"
  ping -n 6 127.0.0.1 >nul
  goto loop
)

:loop
echo [%date% %time%] starting ident-bridge >> "C:\ident-bridge\bridge.log"
"C:\ident-bridge\venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8000 >> "C:\ident-bridge\bridge.log" 2>&1
echo [%date% %time%] exited with code %ERRORLEVEL%, restart in 5s >> "C:\ident-bridge\bridge.log"
ping -n 6 127.0.0.1 >nul
goto loop
