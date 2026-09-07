@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Startup'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d+'\아빠 주식 알림봇.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0launcher.vbs\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0bot.ico,0'; $s.Save(); Write-Host ('PC 를 켤 때 자동 실행되도록 등록했습니다: ' + $d)"
pause
