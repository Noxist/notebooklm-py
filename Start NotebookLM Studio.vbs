Set WshShell = CreateObject("WScript.Shell")
strDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strDir
WshShell.Run "cmd.exe /c """ & strDir & "\Start NotebookLM Studio.bat""", 0, False
Set WshShell = Nothing
