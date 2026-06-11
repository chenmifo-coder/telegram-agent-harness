import os
from telegram import Update
from telegram.ext import ContextTypes
from architect_agent import optimize_python_code

async def handle_telegram_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    CEO 接收所有來自外界的訊息，並決定如何分派任務。
    擁有類似馬斯克 (第一性原理) 與黃仁勳 (極致執行力) 的思維。
    """
    chat_id = update.effective_chat.id
    user_text = update.message.caption or update.message.text or ""
    
    # 回報 CEO 已收到訊息
    status_msg = await context.bot.send_message(
        chat_id=chat_id, 
        text="🧠 [CEO]: 我是本公司的 AI CEO。已收到您的需求，正在以第一性原理拆解任務中..."
    )

    # 檢查是否有夾帶檔案
    if update.message.document:
        doc = update.message.document
        file_name = doc.file_name
        
        # 判斷是否為 Python 檔案
        if file_name.endswith('.py'):
            file_size_kb = doc.file_size / 1024
            
            # 確保這裡的 CEO 發言完整存在，改為傳送新訊息接續顯示
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🧠 [CEO]: 收到名為 `{file_name}` 的 Python 檔案 (大小: {file_size_kb:.2f} KB)。\n"
                f"這是一項技術任務，身為 CEO 我只做最高效的決策。我現在將此專案指派給我們的「資深 Python 架構師」進行效能極致優化！🚀"
            )
            
            # 下載檔案
            file = await context.bot.get_file(doc.file_id)
            file_bytearray = await file.download_as_bytearray()
            code_content = file_bytearray.decode('utf-8')
            
            # 呼叫員工 2：資深架構師
            await optimize_python_code(chat_id, user_text, code_content, file_name, context, status_msg)
            return
        else:
            await context.bot.send_message(chat_id=chat_id, text="🧠 [CEO]: 兄弟，這不是 Python 檔案。我們公司目前專注於顛覆性的 Python 架構優化。請傳送 .py 檔案給我。")
            return

    # 若只有純文字，CEO 親自回應
    await context.bot.send_message(chat_id=chat_id, text="🧠 [CEO]: 收到文字訊息。請傳送您需要優化的 `.py` 程式碼檔案，我會指派架構師為您重構到極致！")
