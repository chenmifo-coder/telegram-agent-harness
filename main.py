import os
import threading
from flask import Flask, request, jsonify
from telegram_utils import send_message, set_webhook
from agent import handle_user_message

app = Flask(__name__)

# 取得 Render 自動產生的公開 URL
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://your-app.onrender.com")
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

@app.route("/health", methods=["GET"])
def health():
    """讓 cron-job.org 每10分鐘 ping，防止休眠"""
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """Telegram 更新入口"""
    update = request.get_json()
    if not update or "message" not in update:
        return "OK", 200
    
    chat_id = update["message"]["chat"]["id"]
    text = update["message"].get("text", "")
    
    # 非同步處理 (避免超過 3 秒回應)
    def handle_async():
        reply = handle_user_message(text)
        send_message(chat_id, reply)
    
    threading.Thread(target=handle_async).start()
    return "OK", 200

@app.before_first_request
def setup():
    """啟動時自動設定 Telegram Webhook"""
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    set_webhook(TELEGRAM_TOKEN, webhook_url)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
