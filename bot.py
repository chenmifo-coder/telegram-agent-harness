import os
import tempfile
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import AsyncOpenAI
from dotenv import load_dotenv

# 載入環境變數 (用於本地測試，Render 部署時會讀取 Render 後台設定的變數)
load_dotenv()

# 初始化 NVIDIA API (NVIDIA NIM 支援 OpenAI SDK 格式)
nv_api_key = os.getenv("NVIDIA_API_KEY")
client = AsyncOpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=nv_api_key
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理 /start 指令"""
    welcome_msg = (
        "👋 你好！我是 **Python 程式碼優化專家** 🚀\n\n"
        "請直接傳送一個 `.py` 檔案給我，我會使用 NVIDIA 的免費 AI 模型幫你分析程式碼，"
        "並提供效能、可讀性以及 PEP 8 規範的優化建議與修改後的程式碼！"
    )
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """處理使用者上傳的檔案"""
    document = update.message.document
    
    # 檢查是否為 Python 檔案
    if not document.file_name.endswith('.py'):
        await update.message.reply_text("⚠️ 格式錯誤：請上傳副檔名為 `.py` 的 Python 檔案喔！")
        return

    # 限制檔案大小 (例如 100KB，避免超過 Token 限制)
    if document.file_size > 100 * 1024:
        await update.message.reply_text("⚠️ 檔案過大：請上傳小於 100KB 的程式碼檔案。")
        return

    status_message = await update.message.reply_text("📥 正在下載您的程式碼，請稍候...")

    try:
        # 下載檔案到暫存區
        file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as temp_file:
            await file.download_to_drive(custom_path=temp_file.name)
            with open(temp_file.name, 'r', encoding='utf-8') as f:
                code_content = f.read()
        
        # 刪除暫存檔
        os.remove(temp_file.name)

        await status_message.edit_text("🧠 正在透過 NVIDIA AI 進行深度分析與優化 (這可能需要幾十秒)...")

        # 呼叫 NVIDIA API (使用 Llama-3.1-70B 模型)
        response = await client.chat.completions.create(
            model="meta/llama-3.1-70b-instruct",
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "你是一個資深的 Python 程式碼優化專家。"
                        "請分析使用者提供的程式碼，指出可以改進的地方（例如：時間複雜度、空間複雜度、可讀性、PEP 8 規範等）。"
                        "接著，請提供優化後的完整程式碼。請務必使用「繁體中文」回答。"
                    )
                },
                {"role": "user", "content": f"請幫我優化以下程式碼：\n```python\n{code_content}\n```"}
            ],
            temperature=0.2,
            max_tokens=3000,
        )

        result_text = response.choices[0].message.content

        # Telegram 單則訊息有 4096 字元限制
        # 如果結果太長，存成 Markdown 檔案回傳給使用者
        if len(result_text) > 4000:
            await status_message.edit_text("✅ 分析完成！因為建議內容較長，我將以檔案形式傳送給您查看。")
            with tempfile.NamedTemporaryFile(delete=False, suffix=".md", mode='w', encoding='utf-8') as res_file:
                res_file.write(result_text)
                res_file_path = res_file.name
            
            with open(res_file_path, 'rb') as f:
                await update.message.reply_document(
                    document=f, 
                    filename=f"優化建議_{document.file_name}.md",
                    caption="這是您的優化報告與程式碼 🚀"
                )
            os.remove(res_file_path)
        else:
            # 長度在限制內，直接發送訊息
            await status_message.edit_text(result_text, parse_mode='Markdown')

    except Exception as e:
        await status_message.edit_text(f"❌ 發生錯誤，無法完成優化：\n`{str(e)}`", parse_mode='Markdown')

def main() -> None:
    """啟動機器人"""
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        print("❌ 錯誤: 請設定 TELEGRAM_TOKEN 環境變數")
        return
    if not os.getenv("NVIDIA_API_KEY"):
        print("❌ 錯誤: 請設定 NVIDIA_API_KEY 環境變數")
        return

    # 建立應用程式
    application = Application.builder().token(token).build()

    # 註冊指令與訊息處理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # 判斷是否在 Render 平台上 (Render Web Service 會自動提供此環境變數)
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    
    if render_url:
        # Webhook 模式 (Web Service)
        port = int(os.environ.get("PORT", "10000"))
        webhook_url = f"{render_url}/{token}"
        print(f"🤖 偵測到 Render 網址，啟動 Webhook 模式 (Port: {port})...")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=token,
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES
        )
    else:
        # 本地開發測試時的輪詢模式
        print("🤖 啟動輪詢 (Polling) 模式...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
