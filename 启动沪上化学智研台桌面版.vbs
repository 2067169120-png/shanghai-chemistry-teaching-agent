Option Explicit

' ASCII-only launcher: current trial, then retained rollback builds.
Dim shell, fs, root, scriptPath, exePath, pythonPath, command, appName, packageName
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
scriptPath = root & "\runtime\deeptutor_shchem\desktop_teacher_workbench.pyw"
appName = ChrW(&H6CAA) & ChrW(&H4E0A) & ChrW(&H5316) & ChrW(&H5B66) & ChrW(&H667A) & ChrW(&H7814) & ChrW(&H53F0)
exePath = ""
For Each packageName In Array("desktop_package_0.1.61", "desktop_package_0.1.60-r2", "desktop_package_0.1.59", "desktop_package_0.1.58", "desktop_package_0.1.57", "desktop_package_0.1.56", "desktop_package_0.1.54", "desktop_package_0.1.53")
  exePath = fs.BuildPath(fs.BuildPath(root & "\runtime\deeptutor_shchem\" & packageName, appName), appName & ".exe")
  If fs.FileExists(exePath) Then Exit For
Next
If fs.FileExists(exePath) Then
  shell.CurrentDirectory = root
  command = Chr(34) & exePath & Chr(34)
  shell.Run command, 1, False
  WScript.Quit 0
End If
If Not fs.FileExists(scriptPath) Then
  MsgBox "Desktop workbench files are missing. Please check the project folder.", 16, "Shanghai Chemistry"
  WScript.Quit 2
End If
If fs.FileExists(root & "\.venv-desktop\Scripts\pythonw.exe") Then
  pythonPath = root & "\.venv-desktop\Scripts\pythonw.exe"
Else
  pythonPath = "pythonw.exe"
End If
shell.CurrentDirectory = root
command = Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & scriptPath & Chr(34)
shell.Run command, 1, False
