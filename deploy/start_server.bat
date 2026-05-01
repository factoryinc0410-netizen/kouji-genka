@echo off
setlocal

REM ================================================================
REM  Factoryskills - Manual Test Startup Script
REM  Press Ctrl+C to stop the server.
REM ================================================================

set "APP_DIR=%~dp0.."
set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"

echo.
echo ======================================================
echo   Factoryskills - Test Startup
echo   Press Ctrl+C to stop
echo ======================================================
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    echo Please run the following commands first:
    echo   cd "%APP_DIR%"
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

echo [INFO] Working directory: %CD%
echo [INFO] Python: %PYTHON_EXE%
echo [INFO] URL: http://localhost:8000/
echo.

"%PYTHON_EXE%" -m uvicorn web_app.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info

echo.
echo Server stopped.
pause
exit /b 0
