@echo off
echo Stopping IdentBridge task and python...
schtasks /End /TN IdentBridge >nul 2>&1
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM uvicorn.exe /T >nul 2>&1
echo Done.
pause
