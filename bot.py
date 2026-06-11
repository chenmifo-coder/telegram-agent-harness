import os
import asyncio
import uvicorn
from fastapi import FastAPI
from telegram.ext import ApplicationBuilder, MessageHandler, filters

# 匯入 CEO 員工 (總發派器)
from ceo_agent import handle_telegram_message

# 初始化 FastAPI 以符合 Render Web Service 需求 (提供 port 讓 cron-job ping)
app = FastAPI()

@app.get("/")
@app.get("/ping")
async def ping():
    return {"status": "AI Agent Company is running 24/7", "CEO": "Online"}

async def run_fastapi():
    """在背景運行 FastAPI 伺服器"""
    port = int(os.environ.get("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    """主程式：啟動 Telegram Bot 與 FastAPI"""
    telegram_token = os.environ.get("TELEGRAM_TOKEN")
    if not telegram_token:
        print("請設定 TELEGRAM_TOKEN 環境變數")
        return

    # 初始化 Telegram Bot
    application = ApplicationBuilder().token(telegram_token).build()
    
    # 接收所有文字與檔案訊息，交給 CEO 處理
    application.add_handler(MessageHandler(filters.TEXT | filters.Document.ALL, handle_telegram_message))

    # 啟動 Bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    print("CEO 已經上線，正在監聽 Telegram 訊息...")

    # 同時啟動 FastAPI 確保 Render Web Service 正常監聽 Port
    asyncio.create_task(run_fastapi())

    # 保持主程式運行
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    # 啟動事件迴圈
    asyncio.run(main())
