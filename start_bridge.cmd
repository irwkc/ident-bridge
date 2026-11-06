@echo off
cd /d "C:\ident-bridge"
"C:\ident-bridge\venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port 8000 >> "C:\ident-bridge\bridge.log" 2>&1