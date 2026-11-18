@echo off
rem Double-click me to start clipping. Close the window to stop.
cd /d "%~dp0"
if not exist "%~dp0clipper\__main__.py" goto :notextracted
set "HERE=%~dp0"
rem Windows runs files opened inside a ZIP from a temp folder; refuse that.
if /i not "%HERE:\AppData\Local\Temp\=%"=="%HERE%" goto :notextracted
if not exist "%~dp0.venv\Scripts\python.exe" goto :nosetup
set "PATH=%~dp0.venv\Scripts;%PATH%"
echo Clipper starting. Clips go to the data\clips folder.
echo Leave this window open. Close it to stop.
echo.
"%~dp0.venv\Scripts\python.exe" -m clipper run %*
echo.
echo The clipper stopped. If you see red text or "Error" above, copy it and send it to Claude.
pause
exit /b

:notextracted
echo.
echo  STOP: you opened this from inside the ZIP file.
echo  Right-click the ZIP, choose "Extract All", then use the files in the extracted folder.
echo.
pause
exit /b 1

:nosetup
echo.
echo  Setup hasn't been run yet. Double-click SETUP-WINDOWS.bat first.
echo.
pause
exit /b 1
