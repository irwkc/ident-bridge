@echo off
:: Shows real python/uvicorn errors in this window.
cd /d "C:\ident-bridge"
echo Folder: %CD%
echo.
if not exist "venv\Scripts\python.exe" (
  echo ERROR: venv\Scripts\python.exe missing
  pause
  exit /b 1
)
if not exist "app.py" (
  echo ERROR: app.py missing
  pause
  exit /b 1
)

echo Checking uvicorn...
venv\Scripts\python.exe -c "import uvicorn; print(uvicorn.__version__)"
echo.
echo Starting bridge on port 8000...
echo If this window stays open with no traceback - it works.
echo.
venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8000
echo.
echo Exited with code %ERRORLEVEL%
pause
