@echo off
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo [오류] 설치가 안 되어 있습니다. install.bat 를 먼저 실행하세요.
  pause
  exit /b 1
)
"%VPY%" -m stockbot scan
pause
