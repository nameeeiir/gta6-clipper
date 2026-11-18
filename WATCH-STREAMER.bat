@echo off
rem Double-click me to clip specific streamers (e.g. ishowspeed), whatever category they're in.
cd /d "%~dp0"
echo.
echo Type the Twitch name of the streamer to clip - the part after twitch.tv/
echo For several, put spaces between them, like:  ishowspeed kaicenat
echo.
set "NAMES="
set /p "NAMES=Streamer name(s): "
if not defined NAMES goto :nonames
set "NAMES=%NAMES:https://www.twitch.tv/=%"
set "NAMES=%NAMES:https://twitch.tv/=%"
set "NAMES=%NAMES:www.twitch.tv/=%"
set "NAMES=%NAMES:twitch.tv/=%"
echo.
echo Watching: %NAMES%   - any category, nothing gets posted.
echo If they're offline it keeps checking every 2 minutes and starts when they go live.
echo.
call "%~dp0START-WINDOWS.bat" --any-category --channels %NAMES%
exit /b

:nonames
echo No name typed. Close this and try again.
pause
exit /b 1
