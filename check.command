#!/bin/bash
source "$(dirname "$0")/mac/common.sh" || exit 1
python -m stockbot check
echo; read -r -p "엔터를 누르면 닫힙니다." _
