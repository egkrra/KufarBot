import os
import requests
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv('.venv/.env')

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
data = {"chat_id": CHAT_ID, "text": "✅ Тестовое сообщение от бота"}

resp = requests.post(url, data=data)
print(resp.status_code, resp.text)
