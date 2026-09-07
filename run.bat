@echo off
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo [오류] 설치가 안 되어 있습니다. install.bat 를 먼저 실행하세요.
  pause
  exit /b 1
)
:loop
"%VPY%" -m stockbot
set "RC=%ERRORLEVEL%"
if "%RC%"=="2" (
  echo config.yaml 에 봇 토큰을 먼저 넣어주세요.
  pause
  exit /b 2
)
if "%RC%"=="10" (
  echo 업데이트 완료. 바로 다시 시작합니다...
  goto loop
)
if "%RC%"=="3" (
  echo 봇이 이미 다른 창에서 실행 중입니다. 이 창은 닫아도 됩니다.
  pause
  exit /b 3
)
echo.
echo 봇이 종료되었습니다. 15초 후 다시 시작합니다... (창을 닫으면 완전히 종료)
timeout /t 15 >nul
goto loop
