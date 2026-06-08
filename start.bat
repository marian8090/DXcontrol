@echo off
REM DXcontrol launcher - double-click to run.
cd /d "%~dp0"

echo Checking MIDI libraries...
py -3 -c "import mido, rtmidi" 2>nul
if errorlevel 1 (
    echo Installing required MIDI libraries (first run only)...
    py -3 -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Could not install dependencies. Make sure you have an internet connection.
        pause
        exit /b 1
    )
)

py -3 dxcontrol.py
if errorlevel 1 pause
