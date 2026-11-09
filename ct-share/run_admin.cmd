@echo off
cd /d "%~dp0"
set CT_SHARE_DIR=%CD%\local-data
python -m ct_share.admin_main
if errorlevel 1 pause
