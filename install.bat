@echo off
cd /d "%~dp0"
echo ======================================
echo   아빠 주식 알림봇 설치
echo ======================================
if not exist config.yaml copy config.example.yaml config.yaml >nul
if not exist logs mkdir logs

set "VPY=%~dp0.venv\Scripts\python.exe"
if exist "%VPY%" goto :pipinstall

echo [1/3] 가상환경 만드는 중...
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m venv .venv
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [오류] Python 이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 Python 3.12 를 설치할 때
    echo "Add python.exe to PATH" 를 체크한 뒤 다시 실행하세요.
    pause
    exit /b 1
  )
  python -m venv .venv
)
if not exist "%VPY%" (
  echo [오류] 가상환경(.venv) 생성에 실패했습니다. 이 창을 찍어서 보내주세요.
  pause
  exit /b 1
)

:pipinstall
echo [2/3] 라이브러리 설치 중... (2~3분, 인터넷 필요)
"%VPY%" -m pip install --upgrade pip > logs\install.log 2>&1
"%VPY%" -m pip install -r requirements.txt >> logs\install.log 2>&1
if errorlevel 1 (
  echo [오류] 라이브러리 설치 실패. 아래 마지막 줄들을 찍어서 보내주세요.
  echo ------------------------------------------------
  type logs\install.log | findstr /i "error"
  echo ------------------------------------------------
  pause
  exit /b 1
)

echo [3/3] 설치 확인 중...
"%VPY%" -c "import yaml, telegram, pandas, FinanceDataReader; print('라이브러리 OK, Python', __import__('sys').version.split()[0])"
if errorlevel 1 (
  echo [오류] 라이브러리 확인 실패. logs\install.log 를 보내주세요.
  pause
  exit /b 1
)

call make_desktop_icon.bat
echo.
echo 설치 완료!  바탕화면의 "아빠 주식 알림봇" 아이콘을 더블클릭하면 켜집니다.
echo (토큰이 없으면 config.yaml 에 먼저 입력하세요)
pause
