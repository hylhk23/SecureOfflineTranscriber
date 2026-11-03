@echo off
rem Checks that every library loads and the window opens, then closes by itself.
cd /d "%~dp0"
"%~dp0python\python.exe" "%~dp0app\OfflineTranscriber.py" --smoke-test
echo.
pause
