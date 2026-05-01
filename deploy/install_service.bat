@echo off
setlocal

REM ================================================================
REM  Factoryskills - Windows Service Install Script (NSSM)
REM
REM  このスクリプトは以下を行います:
REM    1. NSSM を使って Factoryskills を Windows サービスとして登録
REM    2. 作業ディレクトリ・ログ出力先・ログローテーションを設定
REM    3. OS 起動時の自動起動・障害時の自動再起動を設定
REM
REM  前提条件:
REM    - 管理者権限で実行すること
REM    - NSSM がインストール済みで PATH に含まれていること
REM      (https://nssm.cc/ からダウンロード)
REM    - .venv が作成済みであること
REM    - .env が設定済みであること（特に SECRET_KEY と HOST）
REM
REM  ※ 再インストールする場合は先に uninstall_service.bat を実行
REM ================================================================

REM -- Configuration -----------------------------------------------
set "APP_DIR=%~dp0.."
set "SERVICE_NAME=Factoryskills"
set "NSSM_EXE=nssm"
set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
set "UVICORN_ARGS=-m uvicorn web_app.main:app --host 0.0.0.0 --port 8000 --workers 1 --log-level info"
set "LOG_DIR=%APP_DIR%\logs"
set "ROTATE_BYTES=10485760"

echo.
echo ======================================================
echo   Factoryskills - Service Installer
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
    echo.
    echo   Download from: https://nssm.cc/
    echo   Extract nssm.exe to a directory in your PATH, e.g.:
    echo     C:\Windows\System32\nssm.exe
    echo   Or set the NSSM_EXE variable in this script.
    echo.
    pause
    exit /b 1
)

REM -- Python venv check -------------------------------------------
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Python venv not found: %PYTHON_EXE%
    echo.
    echo   Create the venv:
    echo     cd "%APP_DIR%"
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM -- .env check --------------------------------------------------
if not exist "%APP_DIR%\.env" (
    echo [ERROR] .env file not found!
    echo.
    echo   Create from template:
    echo     copy "%APP_DIR%\.env.example" "%APP_DIR%\.env"
    echo   Then edit .env and set SECRET_KEY and HOST.
    echo.
    pause
    exit /b 1
)

REM -- SECRET_KEY safety check -------------------------------------
findstr /C:"SECRET_KEY=change-me" "%APP_DIR%\.env" >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARNING] SECRET_KEY in .env is still the default value!
    echo   Please change it to a random string before production use.
    echo.
    set /p CONTINUE="Continue anyway? (Y/N): "
    if /i not "!CONTINUE!"=="Y" (
        echo Cancelled. Edit .env first.
        pause
        exit /b 1
    )
)

REM -- Create log directory ----------------------------------------
if not exist "%LOG_DIR%" (
    mkdir "%LOG_DIR%"
    echo [INFO] Created log directory: %LOG_DIR%
)

REM -- Check existing service --------------------------------------
%NSSM_EXE% status %SERVICE_NAME% >nul 2>&1
if %errorlevel% equ 0 (
    echo [WARNING] Service "%SERVICE_NAME%" already exists.
    echo   Run uninstall_service.bat first, then re-run this script.
    echo.
    pause
    exit /b 1
)

REM ================================================================
REM  Install & Configure Service
REM ================================================================

echo [1/7] Installing service...
%NSSM_EXE% install %SERVICE_NAME% "%PYTHON_EXE%" %UVICORN_ARGS%
if %errorlevel% neq 0 (
    echo [ERROR] Service installation failed.
    pause
    exit /b 1
)

echo [2/7] Setting working directory...
%NSSM_EXE% set %SERVICE_NAME% AppDirectory "%APP_DIR%"

echo [3/7] Configuring log output...
%NSSM_EXE% set %SERVICE_NAME% AppStdout "%LOG_DIR%\service_stdout.log"
%NSSM_EXE% set %SERVICE_NAME% AppStderr "%LOG_DIR%\service_stderr.log"

echo [4/7] Configuring log rotation (max %ROTATE_BYTES% bytes per file)...
%NSSM_EXE% set %SERVICE_NAME% AppRotateFiles 1
%NSSM_EXE% set %SERVICE_NAME% AppRotateOnline 1
%NSSM_EXE% set %SERVICE_NAME% AppRotateBytes %ROTATE_BYTES%

echo [5/7] Setting service metadata...
%NSSM_EXE% set %SERVICE_NAME% DisplayName "Factoryskills"
%NSSM_EXE% set %SERVICE_NAME% Description "Factoryskills - Business automation web platform (FastAPI/Uvicorn)"
%NSSM_EXE% set %SERVICE_NAME% Start SERVICE_AUTO_START

echo [6/7] Configuring auto-restart on failure...
%NSSM_EXE% set %SERVICE_NAME% AppExit Default Restart
%NSSM_EXE% set %SERVICE_NAME% AppRestartDelay 5000

echo [7/7] Setting environment (inherit .env via Python dotenv)...
%NSSM_EXE% set %SERVICE_NAME% AppEnvironmentExtra PYTHONIOENCODING=utf-8

echo.
echo ======================================================
echo   Service installed successfully!
echo ======================================================
echo.
echo   Service name : %SERVICE_NAME%
echo   Python       : %PYTHON_EXE%
echo   App dir      : %APP_DIR%
echo   Log stdout   : %LOG_DIR%\service_stdout.log
echo   Log stderr   : %LOG_DIR%\service_stderr.log
echo   Auto-start   : Enabled (starts on OS boot)
echo   On failure   : Auto-restart after 5 seconds
echo.
echo   Next steps:
echo     1. Start the service:
echo          nssm start %SERVICE_NAME%
echo        or
echo          net start %SERVICE_NAME%
echo.
echo     2. Open in browser:
echo          http://localhost:8000/
echo.
echo     3. Windows Firewall (if accessing from other PCs):
echo          netsh advfirewall firewall add rule ^
echo            name="Factoryskills" dir=in action=allow ^
echo            protocol=TCP localport=8000
echo.

pause
exit /b 0
