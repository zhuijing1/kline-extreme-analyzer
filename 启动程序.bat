@echo off
rem 启动程序（无控制台窗口）
rem 用 pythonw.exe + start，cmd 窗口立即关闭，任务栏/窗口使用程序自带图标
cd /d "%~dp0"
start "" "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe" "%~dp0kline_extreme_analyzer.py"
exit
