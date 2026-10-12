@echo off
cd /d "%~dp0.."
".runtime\conda\python.exe" -X utf8 "src\python\tools\runtime\start_observer.py" %*
if errorlevel 1 pause
