' 검은 창 없이 백그라운드로 봇 실행 (시작프로그램 폴더에 이 파일의 바로가기를 넣으면 부팅 시 자동 실행)
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c """ & dir & "\run.bat""", 0, False
