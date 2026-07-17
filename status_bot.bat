@echo off
chcp 65001 >nul
echo Checking Kufar Bot status...

tasklist /fi "imagename eq python.exe" >nul && echo ✅ Bot is RUNNING || echo ❌ Bot is NOT running

if exist bot_log.txt (
    echo 📋 Log file: bot_log.txt
) else (
    echo No log file found
)

pause