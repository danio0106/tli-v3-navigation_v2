@echo off
echo ======================================
echo Torchlight Infinite Bot - Install Dependencies
echo ======================================
echo.

REM Check if Python is installed
echo Checking for Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in your PATH!
    echo Please install Python from https://www.python.org/
    echo.
    pause
    exit /b 1
)

echo Python found:
python --version
echo.

REM Install required packages
echo Installing required packages...
echo.

echo Installing customtkinter...
pip install customtkinter
if errorlevel 1 (
    echo WARNING: Failed to install customtkinter
)
echo.

echo Installing pymem...
pip install pymem
if errorlevel 1 (
    echo WARNING: Failed to install pymem
)
echo.

echo Installing psutil...
pip install psutil
if errorlevel 1 (
    echo WARNING: Failed to install psutil
)
echo.

echo Installing opencv-python-headless...
pip install opencv-python-headless
if errorlevel 1 (
    echo WARNING: Failed to install opencv-python-headless
)
echo.

echo Installing numpy...
pip install numpy
if errorlevel 1 (
    echo WARNING: Failed to install numpy
)
echo.

echo Installing mss...
pip install mss
if errorlevel 1 (
    echo WARNING: Failed to install mss
)
echo.

echo ======================================
echo Installation complete!
echo ======================================
pause
