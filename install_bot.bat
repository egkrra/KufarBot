@echo off
chcp 65001 >nul
echo Installing Kufar Bot to startup...

:: Создаем VBS скрипт для скрытого запуска
echo Set WshShell = CreateObject("WScript.Shell") > "%TEMP%\kufar_bot.vbs"
echo WshShell.Run "cmd /c cd /d C:\Users\Egor1\PycharmProjects\KufarBot && .venv\Scripts\python.exe kufar_bot.py", 0, False >> "%TEMP%\kufar_bot.vbs"

:: Добавляем в автозагрузку
copy "%TEMP%\kufar_bot.vbs" "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\kufar_bot.vbs"

echo ✅ Bot installed to startup!
echo ✅ Bot will run automatically on Windows login (hidden).
pause