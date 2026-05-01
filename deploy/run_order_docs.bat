@echo off
setlocal

set "APP_DIR=%~dp0.."
set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"

echo.
echo ======================================================
echo   Order Docs Generator (with GUI)
echo ======================================================
echo.

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    pause
    exit /b 1
)

if "%~1"=="" (
    echo [ERROR] Please specify an Excel file path.
    echo.
    echo Usage:
    echo   %~nx0 "path\to\excel.xlsx"
    echo.
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

echo [INFO] Excel: %~1
echo [INFO] Python: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" run_order_docs.py "%~1"

echo.
pause
exit /b 0
