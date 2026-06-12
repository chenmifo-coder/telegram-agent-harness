import os
import threading
import traceback
import requests
from flask import Flask, request
from telegram_utils import send_message, set_webhook
from agent import handle_user_message

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

# 取代 @app.before_request，在應用程式啟動時主動執行一次 Webhook 註冊
def init_webhook():
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        try:
            result = set_webhook(TELEGRAM_TOKEN, webhook_url)
            print(f"✅ Webhook setup result: {result}")
        except Exception as e:
            print(f"❌ Failed to set webhook: {e}")

# 若是透過 Gunicorn 啟動，會在模組載入時執行
init_webhook()

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json()
    if not update or "message" not in update:
        return "OK", 200

    message = update["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if not text:
        return "OK", 200 # 忽略非文字訊息(如貼圖、照片)

    # 由於 LLM 處理和 GitHub API 呼叫需要時間，必須非同步處理，否則 Telegram Webhook 會 Timeout 重發
    def handle_async():
        try:
            # 傳送處理中提示 (優化使用者體驗)
            send_message(chat_id, "⏳ 收到請求，AI 正在分析並修改程式碼，請稍候...")
            reply = handle_user_message(text)
        except Exception as e:
            error_trace = traceback.format_exc()
            print(f"❌ ERROR in handle_user_message:\n{error_trace}")
            reply = f"❌ 內部錯誤：{str(e)}\n請稍後再試，或檢查系統日誌。"
        
        send_message(chat_id, reply)

    threading.Thread(target=handle_async).start()
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
