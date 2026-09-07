@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut($d+'\아빠 주식 알림봇.lnk'); $s.TargetPath='wscript.exe'; $s.Arguments='\"%~dp0launcher.vbs\"'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0bot.ico,0'; $s.Description='아빠 주식 알림봇 켜기/끄기'; $s.Save(); Write-Host ('바탕화면에 아이콘을 만들었습니다: ' + $d)"
