@echo off
REM ============================================================
REM  Factoryskills - One-Click Stop
REM  NOTE: keep this file pure ASCII. Japanese characters in
REM  a .bat file get mangled by CMD on Japanese Windows
REM  (cp932 vs utf-8 byte-offset parsing bug), which causes
REM  "xxx is not recognized as a command" errors.
REM
REM  Kills the process listening on port 8000.
REM ============================================================

chcp 65001 >nul
title Factoryskills Stop

echo ============================================================
echo  Factoryskills - Stop Server
echo ============================================================
echo.

set "KILLED=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo [INFO] Stopping server process PID=%%a ...
    taskkill /F /PID %%a >nul 2>&1
    if errorlevel 1 (
        echo        [WARN] Failed to kill PID=%%a ^(already gone?^)
    ) else (
        echo        [OK]   Killed PID=%%a
        set "KILLED=1"
    )
)

if "%KILLED%"=="0" (
    echo [INFO] No running server found on port 8000.
) else (
    echo.
    echo ============================================================
    echo  [OK] Factoryskills server stopped.
    echo ============================================================
)

echo.
echo This window will close in 3 seconds...
timeout /t 3 /nobreak >nul
exit /b 0
