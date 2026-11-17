@echo off
rem Double-click me to start clipping. Close the window (or press Ctrl+C) to stop.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run SETUP-WINDOWS.bat first.
    pause
    exit /b 1
)
set "PATH=%~dp0.venv\Scripts;%PATH%"
echo Watching live GTA VI streams. Clips go to data\clips. Close this window to stop.
".venv\Scripts\python.exe" -m clipper run %*
echo.
pause
