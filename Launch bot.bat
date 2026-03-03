@echo off
:: Check if already running as admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: Change to the directory where the batch file lives
cd /d "%~dp0"

echo ======================================
echo Torchlight Infinite Bot - Launcher
echo ======================================
echo Running as Administrator
echo Working directory: %cd%
echo.

echo Attempting to launch the bot...
echo.

python main.py
if errorlevel 1 (
    echo.
    echo ERROR: Failed to launch the bot!
    echo.
    echo Possible solutions:
    echo 1. Run "Install dependencies.bat" to install required packages
    echo 2. Make sure Python is installed and in your PATH
    echo 3. Check that main.py exists in the current directory
    echo.
    pause
    exit /b 1
)
