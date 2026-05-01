@echo off
REM ============================================================
REM  Factoryskills - One-Click Start
REM  NOTE: keep this file pure ASCII. Japanese characters in
REM  a .bat file get mangled by CMD on Japanese Windows
REM  (cp932 vs utf-8 byte-offset parsing bug), which causes
REM  "xxx is not recognized as a command" errors.
REM
REM  1) check venv python
REM  2) purge __pycache__ (stale bytecode can mask source fixes)
REM  3) kill any existing process listening on :8000
REM  4) wait up to 10 seconds for the port to free up
REM  5) open browser and launch the server
REM  6) pause on error so the window does not close
REM ============================================================

chcp 65001 >nul
title Factoryskills Server
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
set "URL=http://localhost:8000"
set "PORT=8000"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    echo Please make sure .venv\Scripts\python.exe exists.
    pause
    exit /b 1
)

REM --- [1/5] Purge project __pycache__ ------------------------
REM Prevents stale .pyc files from masking source edits.
echo [1/5] Purging project __pycache__ folders (Fast Mode)...
"%PYTHON_EXE%" -c "import pathlib, shutil; [shutil.rmtree(p, ignore_errors=True) for p in pathlib.Path('.').rglob('__pycache__') if '.venv' not in p.parts]"
echo.

REM --- [2/5] Kill any existing server on this port ------------
echo [2/5] Checking port %PORT% for old listeners...
set "KILLED=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo        [WARN] Found PID=%%a - sending taskkill /F
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo        [WARN] Failed to kill PID=%%a ^(already gone?^)
    ) else (
        echo        [OK]   Killed PID=%%a
        set "KILLED=1"
    )
)
if "%KILLED%"=="0" (
    echo        No existing server found. Good.
)
echo.

REM --- [3/5] Wait for port release (max 10 seconds) -----------
echo [3/5] Waiting for port %PORT% to be free...
set /a WAIT=0
:WAIT_FREE
netstat -an | findstr ":%PORT%" | findstr "LISTENING" >nul
if %errorlevel% neq 0 goto PORT_FREE
set /a WAIT+=1
if %WAIT% geq 10 (
    echo        [ERROR] Port did not free up after 10 seconds.
    echo        Run stop_system.bat manually, then retry.
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto WAIT_FREE

:PORT_FREE
echo        Port is free.
echo.

REM --- [4/5] Open browser in background (after 3 sec delay) ----
echo [4/5] Opening browser in 3 seconds...
start "" cmd /c "timeout /t 3 /nobreak >nul && start %URL%"
echo.

REM --- [5/5] Launch server (foreground) -----------------------
echo [5/5] Launching server...
echo ------------------------------------------------------------
echo   Close this window or press Ctrl+C to stop the server.
echo ------------------------------------------------------------
echo.

"%PYTHON_EXE%" run_server.py
set "EXITCODE=%errorlevel%"

echo.
echo ------------------------------------------------------------
if "%EXITCODE%"=="0" (
    echo Server stopped normally.
) else (
    echo [ERROR] Server exited with code=%EXITCODE%
    echo Please check the log above.
)
echo ------------------------------------------------------------
pause
exit /b %EXITCODE%
