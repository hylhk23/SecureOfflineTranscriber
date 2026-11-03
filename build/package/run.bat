@echo off
rem Secure Offline Transcriber - start without a console window
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" "%~dp0app\OfflineTranscriber.py" %*
