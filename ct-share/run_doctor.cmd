@echo off
cd /d "%~dp0"
set CT_SHARE_DIR=%CD%\local-data
python -m ct_share.doctor_main
if errorlevel 1 pause
