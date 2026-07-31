' 启动 XtMiniQmt 行情服务（隐藏窗口，独立 Windows 会话）
Dim WshShell, exePath, workDir
Set WshShell = CreateObject("WScript.Shell")

' 绝对路径
exePath = "D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64\XtMiniQmt.exe"
workDir = "D:\迅投极速策略交易系统交易终端 大同证券QMT实盘\bin.x64"

' 切换到工作目录
WshShell.CurrentDirectory = workDir

' 检查文件是否存在
Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")
If fso.FileExists(exePath) Then
    ' 0 = 隐藏窗口, False = 异步执行
    WshShell.Run """" & exePath & """", 0, False
    WScript.Sleep 3000
    WScript.Quit 0
Else
    WScript.Echo "文件不存在: " & exePath
    WScript.Quit 1
End If
