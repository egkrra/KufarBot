@echo off
chcp 65001 >nul
echo Restarting Kufar Bot...

:: Останавливаем старый процесс
taskkill /fi "windowtitle eq kufar_bot*" /f 2>nul

:: Ждем секунду
timeout /t 1 /nobreak >nul

:: Запускаем снова
start /B .venv\Scripts\python.exe kufar_bot.py

echo ✅ Bot restarted!
pause