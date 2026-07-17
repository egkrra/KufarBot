@echo off
chcp 65001 >nul
echo Removing Kufar Bot from startup...

:: Удаляем из автозагрузки
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\kufar_bot.vbs" 2>nul

:: Останавливаем процесс
taskkill /fi "windowtitle eq kufar_bot*" /f 2>nul

echo ✅ Bot removed from startup!
pause