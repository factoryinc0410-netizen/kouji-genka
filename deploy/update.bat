@echo off
setlocal

REM ================================================================
REM  Factoryskills - One-Click Update Script
REM
REM  開発PCで git push した後、サーバー上でこのバッチを実行すると
REM  最新コードを取得 → 依存更新 → サービス再起動 → 起動確認
REM  を一発で行います。ダウンタイムは 5〜10 秒程度です。
REM
REM  ※ 管理者権限で実行してください（サービス再起動に必要）
REM ================================================================

set "APP_DIR=%~dp0.."
set "SERVICE_NAME=Factoryskills"
set "NSSM_EXE=nssm"
set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
set "PIP_EXE=%APP_DIR%\.venv\Scripts\pip.exe"
set "HEALTH_URL=http://localhost:8000/health"
set "MAX_RETRIES=10"
set "RETRY_WAIT=3"

echo.
echo ======================================================
echo   Factoryskills - Updating...
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

REM -- Python venv check -------------------------------------------
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

REM ================================================================
REM  Step 1: Git Pull
REM ================================================================
echo [1/4] Pulling latest changes from git...
git pull origin main
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] git pull failed!
    echo   Possible causes:
    echo     - Local changes conflict with remote
    echo     - Network unreachable
    echo     - Git not installed or not in PATH
    echo.
    echo   To force-reset to remote (DESTROYS local changes):
    echo     git fetch origin main
    echo     git reset --hard origin/main
    echo.
    pause
    exit /b 1
)
echo.

REM ================================================================
REM  Step 2: Update Dependencies
REM ================================================================
echo [2/4] Updating Python dependencies...
"%PIP_EXE%" install -r requirements.txt --quiet --disable-pip-version-check
if %errorlevel% neq 0 (
    echo [WARNING] pip install had errors (non-fatal, continuing...)
)
echo.

REM ================================================================
REM  Step 3: Restart Service
REM ================================================================
echo [3/4] Restarting service...

REM -- Check if NSSM is available ----------------------------------
where %NSSM_EXE% >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] NSSM not found in PATH.
    echo   Service restart skipped.
    echo   If running manually, please restart the server process.
    echo   Download NSSM from https://nssm.cc/
    echo.
    goto :health_check
)

REM -- Check if service is installed --------------------------------
%NSSM_EXE% status %SERVICE_NAME% >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Service "%SERVICE_NAME%" is not installed.
    echo   Run deploy\install_service.bat first.
    echo.
    goto :health_check
)

REM -- Restart (stop → wait → start for clean shutdown) ------------
echo   Stopping %SERVICE_NAME%...
%NSSM_EXE% stop %SERVICE_NAME% >nul 2>&1
timeout /t 3 /nobreak >nul

echo   Starting %SERVICE_NAME%...
%NSSM_EXE% start %SERVICE_NAME% >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Service start failed!
    echo   Check logs: %APP_DIR%\logs\service_stderr.log
    pause
    exit /b 1
)
echo   Service restarted.
echo.

REM ================================================================
REM  Step 4: Health Check
REM ================================================================
:health_check
echo [4/4] Verifying server is running...

REM -- Wait for server startup -------------------------------------
timeout /t 3 /nobreak >nul

set "ATTEMPT=0"
:retry_loop
set /a ATTEMPT+=1
if %ATTEMPT% gtr %MAX_RETRIES% goto :health_fail

REM -- Try curl first, fall back to PowerShell ---------------------
where curl >nul 2>&1
if %errorlevel% equ 0 (
    curl -sf -o nul -w "" "%HEALTH_URL%" >nul 2>&1
    if %errorlevel% equ 0 goto :health_ok
) else (
    powershell -Command "try { $r = Invoke-WebRequest -Uri '%HEALTH_URL%' -UseBasicParsing -TimeoutSec 5; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
    if %errorlevel% equ 0 goto :health_ok
)

echo   Attempt %ATTEMPT%/%MAX_RETRIES% - waiting %RETRY_WAIT%s...
timeout /t %RETRY_WAIT% /nobreak >nul
goto :retry_loop

:health_ok
echo.
echo ======================================================
echo   Update complete! Server is running.
echo ======================================================
echo.
echo   Health check: %HEALTH_URL% ... OK
echo   Updated at:   %date% %time%
echo.
pause
exit /b 0

:health_fail
echo.
echo ======================================================
echo   [WARNING] Server did not respond after %MAX_RETRIES% attempts.
echo ======================================================
echo.
echo   The update was applied, but the server may not have started.
echo   Check the logs:
echo     %APP_DIR%\logs\service_stdout.log
echo     %APP_DIR%\logs\service_stderr.log
echo.
echo   Manual start:
echo     nssm start %SERVICE_NAME%
echo   or
echo     cd "%APP_DIR%" ^&^& .venv\Scripts\python.exe run_server.py
echo.
pause
exit /b 1
