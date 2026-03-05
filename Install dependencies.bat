@echo off
echo ======================================
echo Torchlight Infinite Bot - Install Dependencies
echo ======================================
echo.

REM Check if Python is installed
echo Checking for Python installation...
set "PYTHON_EXE=%cd%\.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in your PATH!
    echo Please install Python from https://www.python.org/
    echo.
    pause
    exit /b 1
)

echo Python found:
"%PYTHON_EXE%" --version
echo.

REM Install required packages
echo Installing required packages...
echo.

echo Installing customtkinter...
"%PYTHON_EXE%" -m pip install customtkinter
if errorlevel 1 (
    echo WARNING: Failed to install customtkinter
)
echo.

echo Installing PySide6...
"%PYTHON_EXE%" -m pip install PySide6
if errorlevel 1 (
    echo WARNING: Failed to install PySide6
)
echo.

echo Installing pymem...
"%PYTHON_EXE%" -m pip install pymem
if errorlevel 1 (
    echo WARNING: Failed to install pymem
)
echo.

echo Installing psutil...
"%PYTHON_EXE%" -m pip install psutil
if errorlevel 1 (
    echo WARNING: Failed to install psutil
)
echo.

echo Installing opencv-python-headless...
"%PYTHON_EXE%" -m pip install opencv-python-headless
if errorlevel 1 (
    echo WARNING: Failed to install opencv-python-headless
)
echo.

echo Installing numpy...
"%PYTHON_EXE%" -m pip install numpy
if errorlevel 1 (
    echo WARNING: Failed to install numpy
)
echo.

echo Installing mss...
"%PYTHON_EXE%" -m pip install mss
if errorlevel 1 (
    echo WARNING: Failed to install mss
)
echo.

echo ======================================
echo Installation complete!
echo ======================================
pause
