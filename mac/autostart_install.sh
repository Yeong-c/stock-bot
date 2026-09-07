#!/bin/bash
# 로그인 시 자동 실행 + 죽으면 자동 재시작 (launchd LaunchAgent 등록)
# 사용: bash mac/autostart_install.sh      해제: bash mac/autostart_remove.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.dadstockbot.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "[오류] $PY 가 없습니다. install.command 를 먼저 실행하세요."; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-m</string><string>stockbot</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string><key>LANG</key><string>ko_KR.UTF-8</string></dict>
</dict>
</plist>
PL
plutil -lint "$PLIST" >/dev/null || { echo "[오류] plist 생성 실패"; exit 1; }
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST" && echo "등록 완료: 로그인 시 자동 실행됩니다. 상태: launchctl print gui/$(id -u)/$LABEL | head -5"
echo "로그: $ROOT/logs/bot.log  (해제: bash mac/autostart_remove.sh)"
