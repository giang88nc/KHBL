' run_hidden_khbl.vbs - Chay 1 lenh cmd AN HOAN TOAN (khong cua so, khong taskbar)
' Dung boi TURN_ON_KHBL.bat:  wscript //B run_hidden_khbl.vbs "lenh can chay"
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c " & WScript.Arguments(0), 0, False
