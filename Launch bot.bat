@echo off
setlocal EnableExtensions

:: Change to the directory where the batch file lives
cd /d "%~dp0"

echo ======================================
echo Torchlight Infinite Bot - Launcher
echo ======================================
echo Working directory: %cd%
echo.

echo Attempting to launch the bot...
echo.

set "PYTHON_EXE=%cd%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" scripts\fast_launcher.py
if errorlevel 1 (
    echo.
    echo ERROR: Bot launch failed.
    echo See launcher error details above.
    echo.
    pause
    exit /b 1
)

endlocal
