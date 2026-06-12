import os
import requests

def send_message(chat_id, text):
    token = os.environ["TELEGRAM_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": text}
    requests.post(url, json=data)

def set_webhook(token, webhook_url):
    url = f"https://api.telegram.org/bot{token}/setWebhook"
    data = {"url": webhook_url}
    resp = requests.post(url, json=data)
    return resp.json()
