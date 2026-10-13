@echo off
cd /d "%~dp0.."
".runtime\conda\python.exe" -X utf8 "src\python\tools\runtime\manage_services.py" %*
