Option Explicit

Dim shell, fs, root, scriptPath, pythonPath, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
scriptPath = root & "\runtime\deeptutor_shchem\close_desktop_workbench.pyw"
If Not fs.FileExists(scriptPath) Then
  scriptPath = root & "\close_desktop_workbench.pyw"
End If
If Not fs.FileExists(scriptPath) Then
  MsgBox "The desktop close helper is missing. Close the workbench window directly.", 48, "Shanghai Chemistry"
  WScript.Quit 1
End If
If fs.FileExists(root & "\.venv-desktop\Scripts\pythonw.exe") Then
  pythonPath = root & "\.venv-desktop\Scripts\pythonw.exe"
Else
  pythonPath = "pythonw.exe"
End If
shell.CurrentDirectory = root
command = Chr(34) & pythonPath & Chr(34) & " " & Chr(34) & scriptPath & Chr(34)
exitCode = shell.Run(command, 0, True)
If exitCode <> 0 Then
  MsgBox "No desktop workbench window was found.", 48, "Shanghai Chemistry"
End If
