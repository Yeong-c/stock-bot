#!/bin/bash
# 맥 설치: 더블클릭하면 터미널이 열리며 실행됩니다.
cd "$(dirname "$0")" || exit 1
echo "======================================"
echo "  아빠 주식 알림봇 설치 (macOS)"
echo "======================================"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[오류] python3 가 없습니다. https://www.python.org/downloads/macos/ 에서 Python 3.12 설치 후 다시 실행하세요."
  read -r -p "엔터를 누르면 닫힙니다." _; exit 1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "python3 $PYV 사용"
[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt || { echo "[오류] 라이브러리 설치 실패. 인터넷 연결을 확인하세요."; read -r -p "엔터를 누르면 닫힙니다." _; exit 1; }
chmod +x ./*.command mac/*.sh 2>/dev/null
echo
echo "설치 완료!  config.yaml 에 봇 토큰을 넣은 뒤  check.command  로 점검하고  run.command  로 실행하세요."
read -r -p "엔터를 누르면 닫힙니다." _
