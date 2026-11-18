@echo off
rem Double-click me once to install everything and enter your keys.
cd /d "%~dp0"
if not exist "%~dp0scripts\windows-setup.ps1" goto :notextracted
set "HERE=%~dp0"
rem Windows runs files opened inside a ZIP from a temp folder; refuse that.
if /i not "%HERE:\AppData\Local\Temp\=%"=="%HERE%" goto :notextracted
set "CLIPPER_FROM_BAT=1"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows-setup.ps1"
echo.
echo This window stays open so you can read it. Press any key to close it.
pause >nul
exit /b

:notextracted
echo.
echo  STOP: you opened this from inside the ZIP file, so the other files are missing.
echo.
echo  1. Close this window.
echo  2. Right-click the ZIP file and choose "Extract All", then click Extract.
echo  3. Open the new folder that appears and double-click SETUP-WINDOWS.bat in there.
echo.
pause
exit /b 1
