@echo off
chcp 65001 >nul
title Factory Platform - 起動中...

echo.
echo  ╔══════════════════════════════════════╗
echo  ║     Factory Platform を起動します     ║
echo  ╚══════════════════════════════════════╝
echo.

:: バックエンド起動
echo [1/2] バックエンド (API) を起動中...
cd /d "C:\Users\factory\Factoryskills\chat\backend"
start "Factory-API" cmd /c "venv\Scripts\uvicorn.exe main:app --host 0.0.0.0 --port 8001 --reload"
timeout /t 2 /nobreak >nul

:: フロントエンド起動
echo [2/2] フロントエンド (Next.js) を起動中...
cd /d "C:\Users\factory\Factoryskills\chat\frontend"
start "Factory-Frontend" cmd /c "npx next dev --port 3000"
timeout /t 4 /nobreak >nul

:: ブラウザ起動
echo.
echo  ブラウザを開いています...
start http://localhost:3000

echo.
echo  ════════════════════════════════════════
echo   Factory Platform が起動しました！
echo   URL: http://localhost:3000
echo  ════════════════════════════════════════
echo.
echo   このウィンドウを閉じてもサーバーは動作し続けます。
echo   停止するには、タスクバーの "Factory-API" と
echo   "Factory-Frontend" のウィンドウを閉じてください。
echo.
pause
