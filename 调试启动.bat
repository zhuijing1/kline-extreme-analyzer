@echo off
rem 调试启动（保留控制台窗口，出错时可以看到日志）
cd /d "%~dp0"
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" kline_extreme_analyzer.py
pause
