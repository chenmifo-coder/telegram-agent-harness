import os
import threading
import traceback
from flask import Flask, request
from telegram_utils import send_message, set_webhook
from agent import handle_user_message

app = Flask(__name__)
webhook_configured = False

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()
    if not update or "message" not in update:
        return "OK", 200

    chat_id = update["message"]["chat"]["id"]
    text = update["message"].get("text", "")

    def handle_async():
        try:
            reply = handle_user_message(text)
        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"❌ ERROR in handle_user_message:\n{error_trace}")
            reply = f"❌ 內部錯誤：{str(e)}\n請稍後再試，或檢查 Render 日誌。"
        send_message(chat_id, reply)

    threading.Thread(target=handle_async).start()
    return "OK", 200

@app.before_request
def setup_webhook():
    global webhook_configured
    if not webhook_configured and RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        set_webhook(TELEGRAM_TOKEN, webhook_url)
        webhook_configured = True
        print(f"✅ Webhook set to {webhook_url}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
