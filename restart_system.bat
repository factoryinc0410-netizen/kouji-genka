@echo off
REM ============================================================
REM  Factoryskills - One-Click Restart
REM  NOTE: keep this file pure ASCII. Japanese characters in
REM  a .bat file get mangled by CMD on Japanese Windows
REM  (cp932 vs utf-8 byte-offset parsing bug), which causes
REM  "xxx is not recognized as a command" errors.
REM
REM  1) stop the server listening on port 8000
REM  2) wait for the port to be released
REM  3) launch start_system.bat in a new window
REM ============================================================

chcp 65001 >nul
title Factoryskills Restart

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

echo ============================================================
echo  Factoryskills - Restart
echo ============================================================
echo.

REM --- [1/3] Stop existing server ------------------------------
echo [1/3] Stopping existing server...

set "KILLED=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo        [WARN] Failed to kill PID=%%a ^(already gone?^)
    ) else (
        echo        [OK]   Killed PID=%%a
        set "KILLED=1"
    )
)

if "%KILLED%"=="0" (
    echo        No running server found.
)
echo.

REM --- [2/3] Wait for port 8000 to be released -----------------
echo [2/3] Waiting for port 8000 to be released...

set /a WAIT=0
:WAIT_FREE
netstat -an | findstr ":8000" | findstr "LISTENING" >nul
if %errorlevel% neq 0 goto PORT_FREE
set /a WAIT+=1
if %WAIT% geq 10 (
    echo        [WARN] Port still busy after 10 seconds. Forcing continue.
    goto PORT_FREE
)
timeout /t 1 /nobreak >nul
goto WAIT_FREE

:PORT_FREE
echo        Port released.
echo.

REM --- [3/3] Launch start_system.bat in a NEW window -----------
echo [3/3] Launching server in a new window...
echo.
start "Factoryskills" cmd /c "%APP_DIR%\start_system.bat"

echo ============================================================
echo  Launched in a new window. You can close this window.
echo ============================================================
echo.
timeout /t 3 /nobreak >nul
exit /b 0
