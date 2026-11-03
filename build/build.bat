@echo off
rem Builds dist\SecureOfflineTranscriber-portable-win64.zip (top folder SecureOfflineTranscriber)
rem Needs Python 3.10 or newer on this PC (any edition; it only runs pip and copies files) and internet.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 build.py %*
) else (
  python build.py %*
)
echo.
pause
