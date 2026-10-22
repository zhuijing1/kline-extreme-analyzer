# ============================================================================
# 更新桌面快捷方式的图标与启动方式
# ----------------------------------------------------------------------------
# 用法: powershell -ExecutionPolicy Bypass -File 更新图标.ps1
# ============================================================================

$appDir = "D:\dsh\dsh\K线极值统计工具"
$icon_path = Join-Path $appDir "kline_analyzer.ico"
$script_path = Join-Path $appDir "kline_extreme_analyzer.py"
$pythonw = "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
$desktop = [System.Environment]::GetFolderPath('Desktop')

# 查找桌面上的快捷方式（可能的文件名）
$possible_names = @(
    "K线极值分析.lnk",
    "K线极值分析程序.lnk",
    "启动程序.lnk",
    "kline_extreme_analyzer.lnk"
)

$shortcut_path = $null
foreach ($name in $possible_names) {
    $path = Join-Path $desktop $name
    if (Test-Path $path) { $shortcut_path = $path; break }
}

if (-not $shortcut_path) {
    Write-Host "未在桌面找到快捷方式，桌面上的快捷方式：" -ForegroundColor Yellow
    Get-ChildItem -Path $desktop -Filter "*.lnk" | Select-Object Name
    exit 1
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcut_path)

# 图标（与任务栏一致）
$shortcut.IconLocation = "$icon_path,0"
# 无控制台启动：直接指向 pythonw.exe
$shortcut.Targetpath = $pythonw
$shortcut.Arguments = "`"$script_path`""
$shortcut.WorkingDirectory = $appDir
$shortcut.Description = "A股K线极值频率统计工具"
$shortcut.Save()

Write-Host "[OK] 快捷方式已更新: $shortcut_path" -ForegroundColor Green
Write-Host "  图标: $icon_path" -ForegroundColor Cyan
Write-Host "  目标: $pythonw" -ForegroundColor Cyan
Write-Host "  参数: $script_path" -ForegroundColor Cyan

# 通知资源管理器刷新图标缓存
if (Get-Command ie4uinit.exe -ErrorAction SilentlyContinue) {
    ie4uinit.exe -show | Out-Null
}
