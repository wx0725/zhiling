@echo off
rem Start the workflow web page (see tools\start.ps1). Close this window to stop the page server.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start.ps1"
