' ============================================================================
'  Launch KLine Extreme Analyzer with NO console window.
'  NOTE: keep this file pure ASCII - VBScript reads files as ANSI, so UTF-8
'        Chinese comments would break the parser.
'  NOTE: build the command line in a variable first - inlining the quote
'        escapes inside shell.Run(...) confuses the VBScript parser.
' ============================================================================
Option Explicit

Dim shell, fso, workDir, pythonw, script, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

workDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
script = workDir & "\kline_extreme_analyzer.py"

If Not fso.FileExists(pythonw) Then
    MsgBox "Python not found:" & vbCrLf & pythonw, vbExclamation, "KLine Analyzer"
    WScript.Quit 1
End If

cmd = """" & pythonw & """ """ & script & """"

' 0 = hidden window, False = do not wait
shell.CurrentDirectory = workDir
shell.Run cmd, 0, False