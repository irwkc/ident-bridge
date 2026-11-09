@echo off
cd /d "%~dp0"
python -m pip install -r requirements-build.txt
pyinstaller --noconfirm CTShareAdmin.spec
pyinstaller --noconfirm CTShareDoctor.spec
mkdir release 2>nul
copy /Y dist\CTShareAdmin.exe release\CTShareAdmin.exe
copy /Y dist\CTShareDoctor.exe release\CTShareDoctor.exe
echo.
echo Ready: release\CTShareAdmin.exe
echo Ready: release\CTShareDoctor.exe
pause
