' 바탕화면 아이콘이 실행하는 파일: 검은 창 없이 봇 실행 창(launcher.py)을 띄운다
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = dir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then
  MsgBox "먼저 install.bat 를 실행해서 설치해 주세요.", 48, "아빠 주식 알림봇"
  WScript.Quit 1
End If
CreateObject("WScript.Shell").Run """" & pyw & """ """ & dir & "\launcher.py""", 0, False
