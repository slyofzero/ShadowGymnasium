@echo off
setlocal

REM Find virtual environment python or system python
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

if "%1"=="" goto frida
if /I "%1"=="frida" goto frida
if /I "%1"=="frida-watch" goto frida_watch
if /I "%1"=="status" goto status
if /I "%1"=="screenshot" goto screenshot
if /I "%1"=="actions" goto actions
if /I "%1"=="action" goto actions
if /I "%1"=="telemetry" goto telemetry
if /I "%1"=="engine" goto engine
if /I "%1"=="tick" goto tick
if /I "%1"=="help" goto help

echo [Error] Unknown target: %1
goto help

:actions
%PYTHON% scripts/game_actions.py %2 %3 %4 %5
goto end

:telemetry
%PYTHON% scripts/stream_telemetry.py %2 %3 %4 %5
goto end

:engine
%PYTHON% scripts/engine_controller.py %2 %3 %4 %5
goto end

:tick
%PYTHON% scripts/tick_controller.py %2 %3 %4 %5
goto end

:frida
%PYTHON% scripts/start_frida_service.py %2 %3 %4
goto end

:frida_watch
%PYTHON% scripts/start_frida_service.py --watch %2 %3 %4
goto end

:screenshot
%PYTHON% .agents/skills/bluestacks-instant-screenshot/scripts/screenshot.py --artifact current_screen
goto end

:status
%PYTHON% -c "from scripts.start_frida_service import find_adb, get_connected_device, is_app_running, DEFAULT_PACKAGE; adb = find_adb(); dev = get_connected_device(adb); run, pid = is_app_running(adb, dev, DEFAULT_PACKAGE) if dev else (False, None); print(f'ADB Device : {dev}'); print(f'App Running: {run} (PID: {pid})')"
goto end

:help
echo.
echo Shadow Fight 2 Automation Harness
echo ==================================
echo Usage:
echo   .\make.bat frida       - Verify and establish Frida bridge ^& port forwarding
echo   .\make.bat frida-watch - Run continuous Frida connection watchdog daemon
echo   .\make.bat screenshot  - Instant sub-second screen capture to artifacts
echo   .\make.bat status      - Check device, installation, and running status
echo   .\make.bat actions [start^|pause^|resume^|exit^|status] - In-engine combat flow
echo   .\make.bat telemetry [--pretty^|--hits-only]           - Live 60Hz JSON state stream
echo   .\make.bat engine      - Interactive combat action controller
echo   .\make.bat tick [freeze^|unfreeze^|step N^|speed N^|status] - Master simulation clock
echo.

:end
endlocal
