@echo off
rem Updates app\models.json: fills in revisions, sizes and SHA-256 from Hugging Face, checks the list.
rem Examples:  update-model-list.bat
rem            update-model-list.bat --refresh whisper-small
rem            update-model-list.bat --check
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update-model-list.py %*
) else (
  python update-model-list.py %*
)
echo.
pause
