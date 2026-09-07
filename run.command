#!/bin/bash
# 봇 실행 (터미널 창을 닫으면 종료). 죽으면 15초 후 자동 재시작.
source "$(dirname "$0")/mac/common.sh" || exit 1
while true; do
  python -m stockbot
  code=$?
  if [ $code -eq 10 ]; then
    echo "업데이트 완료. 바로 다시 시작합니다..."; continue
  fi
  if [ $code -eq 2 ]; then
    echo "config.yaml 의 bot_token 을 먼저 넣어주세요."
    read -r -p "엔터를 누르면 닫힙니다." _; exit 2
  fi
  echo; echo "봇이 종료되었습니다 (code $code). 15초 후 다시 시작합니다... (창을 닫으면 완전히 종료)"
  sleep 15
done
