@echo off
echo Creating taskbar-pinnable shortcut for Torchlight Bot...

set "SCRIPT_DIR=%~dp0"
set "SHORTCUT_PATH=%SCRIPT_DIR%Torchlight Bot.lnk"
set "BAT_PATH=%SCRIPT_DIR%Launch bot.bat"

powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$s = $ws.CreateShortcut('%SHORTCUT_PATH%'); " ^
  "$s.TargetPath = 'cmd.exe'; " ^
  "$s.Arguments = '/c \"\"' + '%BAT_PATH%' + '\"\"'; " ^
  "$s.WorkingDirectory = '%SCRIPT_DIR%'; " ^
  "$s.Description = 'Torchlight Infinite Bot'; " ^
  "$s.Save()"

if exist "%SHORTCUT_PATH%" (
    echo.
    echo SUCCESS: Shortcut created at:
    echo   %SHORTCUT_PATH%
    echo.
    echo You can now right-click "Torchlight Bot.lnk" and choose "Pin to taskbar"
) else (
    echo.
    echo ERROR: Failed to create shortcut
)
echo.
pause
