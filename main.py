import os
import threading
import traceback
from flask import Flask, request
from telegram_utils import send_message, set_webhook
from agent import handle_user_message
from config import TELEGRAM_TOKEN, RENDER_EXTERNAL_URL

app = Flask(__name__)

def init_webhook():
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        try:
            result = set_webhook(TELEGRAM_TOKEN, webhook_url)
            print(f"✅ Webhook 設定結果: {result}")
        except Exception as e:
            print(f"❌ Webhook 設定失敗: {e}")

# 在 Gunicorn 啟動時執行（模組載入階段）
init_webhook()

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Bot is running!", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()
    if not update or "message" not in update:
        return "OK", 200

    chat_id = update["message"]["chat"]["id"]
    text = update["message"].get("text", "")
    if not text:
        return "OK", 200

    def handle_async():
        try:
            send_message(chat_id, "⏳ 收到請求，AI 正在處理...")
            reply = handle_user_message(text)
        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"❌ 處理錯誤:\n{error_trace}")
            reply = f"❌ 內部錯誤：{str(e)}"
        send_message(chat_id, reply)

    threading.Thread(target=handle_async).start()
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
