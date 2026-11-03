@echo off
rem Secure Offline Transcriber - start with a console that shows the log (for troubleshooting)
cd /d "%~dp0"
"%~dp0python\python.exe" "%~dp0app\OfflineTranscriber.py" --debug %*
echo.
echo The program has closed. Log files are in the data\logs folder.
pause
