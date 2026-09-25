@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-active-observation-lab.ps1" %*
set "LAB_EXIT=%ERRORLEVEL%"
pause
exit /b %LAB_EXIT%
