import os
import requests
from config import TELEGRAM_TOKEN

def send_message(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    requests.post(url, json=data)

def set_webhook(token: str, webhook_url: str):
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    data = {"url": webhook_url}
    resp = requests.post(url, json=data)
    return resp.json()
