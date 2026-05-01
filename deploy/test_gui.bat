@echo off
setlocal

set "APP_DIR=%~dp0.."
set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"

echo.
echo ======================================================
echo   GUI Test
echo ======================================================
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

"%PYTHON_EXE%" test_gui.py

echo.
pause
exit /b 0
