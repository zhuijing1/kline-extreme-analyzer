#!/usr/bin/env python3
"""
创建/更新桌面快捷方式（无控制台窗口 + 自定义图标）
==================================================

要点：
  * 目标指向 pythonw.exe（无控制台窗口），而不是 .bat
  * 图标指向 kline_analyzer.ico，桌面图标与任务栏图标一致
  * 桌面路径用 WScript.Shell.SpecialFolders("Desktop") 解析，
    兼容桌面被重定向到其他磁盘的情况（本机桌面在 D:\\desktop）

用法: python 创建快捷方式.py
"""

from __future__ import annotations

import os

PYTHONW = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
SHORTCUT_NAME = "K线极值分析.lnk"


def desktop_dir() -> str:
    """获取真实的桌面目录（桌面可能被重定向到其它磁盘）。"""
    try:
        from win32com.client import Dispatch
        path = Dispatch("WScript.Shell").SpecialFolders("Desktop")
        if path and os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def create_shortcut() -> str:
    """创建桌面快捷方式，返回快捷方式路径。"""
    work_dir = os.path.dirname(os.path.abspath(__file__))
    script = os.path.join(work_dir, "kline_extreme_analyzer.py")
    icon_file = os.path.join(work_dir, "kline_analyzer.ico")

    desktop = desktop_dir()
    shortcut_path = os.path.join(desktop, SHORTCUT_NAME)

    import pythoncom  # noqa: F401  (确保 COM 已初始化)
    from win32com.client import Dispatch

    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortcut(shortcut_path)
    shortcut.Targetpath = PYTHONW
    shortcut.Arguments = f'"{script}"'
    shortcut.WorkingDirectory = work_dir
    shortcut.IconLocation = f"{icon_file},0"
    shortcut.Description = "A股K线极值频率统计工具"
    shortcut.WindowStyle = 1          # 正常显示
    shortcut.save()

    print(f"[OK] 快捷方式已创建: {shortcut_path}")
    print(f"  目标: {PYTHONW}")
    print(f"  参数: {script}")
    print(f"  图标: {icon_file}")
    print("  窗口: 无控制台（pythonw）")
    return shortcut_path


if __name__ == "__main__":
    try:
        create_shortcut()
    except ImportError:
        print("[ERROR] 需要安装 pywin32")
        print("请运行: pip install pywin32")
