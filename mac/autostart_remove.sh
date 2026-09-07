#!/bin/bash
LABEL="com.dadstockbot.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$PLIST"
echo "자동 실행 해제 완료"
