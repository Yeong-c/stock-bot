#!/bin/bash
# 공통: 프로젝트 루트로 이동하고 가상환경 활성화
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
if [ ! -d .venv ]; then
  echo "[안내] 가상환경이 없습니다. 먼저 install.command 를 실행하세요."
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
