@echo off
setlocal

REM ================================================================
REM  Factoryskills - Windows Service Uninstall Script
REM  Safely stops and removes the service.
REM ================================================================

set "SERVICE_NAME=Factoryskills"
set "NSSM_EXE=nssm"

echo.
echo ======================================================
echo   Factoryskills - Service Uninstaller
echo ======================================================
echo.

REM -- Admin privilege check ---------------------------------------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Administrator privileges required.
    echo Right-click and select "Run as administrator".
    echo.
    pause
    exit /b 1
)

REM -- NSSM check --------------------------------------------------
where %NSSM_EXE% >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] NSSM not found.
    pause
    exit /b 1
)

REM -- Check service exists ----------------------------------------
%NSSM_EXE% status %SERVICE_NAME% >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Service "%SERVICE_NAME%" is not installed.
    echo.
    pause
    exit /b 0
)

REM -- Confirm -----------------------------------------------------
echo Service "%SERVICE_NAME%" will be stopped and removed.
echo.
set /p CONFIRM="Continue? (Y/N): "
if /i not "%CONFIRM%"=="Y" (
    echo Cancelled.
    pause
    exit /b 0
)

REM -- Stop service ------------------------------------------------
echo.
echo [1/2] Stopping service...
%NSSM_EXE% stop %SERVICE_NAME% >nul 2>&1
if %errorlevel% equ 0 (
    echo       Stopped.
) else (
    echo       Service was already stopped.
)

timeout /t 3 /nobreak >nul

REM -- Remove service ----------------------------------------------
echo [2/2] Removing service...
%NSSM_EXE% remove %SERVICE_NAME% confirm
if %errorlevel% equ 0 (
    echo.
    echo ======================================================
    echo   Service removed successfully.
    echo ======================================================
    echo.
    echo   Log files in logs/ were preserved.
    echo   Delete manually if no longer needed.
) else (
    echo.
    echo [ERROR] Failed to remove service.
    echo Check services.msc for current status.
)

echo.
pause
exit /b 0
