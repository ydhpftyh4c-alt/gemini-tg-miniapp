@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ╔══════════════════════════════════════════════════════╗
echo ║         Gemini Telegram Mini App Launcher            ║
echo ╚══════════════════════════════════════════════════════╝
echo.
echo [*] Запуск сервера и Cloudflare HTTPS туннеля...
echo.

python run_with_tunnel.py
pause
