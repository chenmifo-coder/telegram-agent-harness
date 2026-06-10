"""
Telegram Agent Harness — Render Free Web Service
修正：多模型 fallback + 詳細錯誤回報
"""
import os, io, logging, asyncio
from telegram import Update, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY  = os.environ["NVIDIA_API_KEY"]
RENDER_URL      = os.environ["RENDER_URL"].rstrip("/")
PORT            = int(os.environ.get("PORT", 10000))

# 防呆機制：如果抓不到 Key，直接終止程式並給予明確提示
if not NVIDIA_API_KEY:
    raise ValueError("啟動失敗：找不到 NVIDIA_API_KEY！請檢查 Render 的 Environment 標籤頁設定。")

# 按優先順序 fallback，確保至少一個可用
NVIDIA_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-nano-30b-a3b",
]

WAIT_FILE, WAIT_PROMPT = range(2)

# 參數配置工廠 (處理特定模型的專屬參數)
def get_model_kwargs(model_name: str) -> dict:
    """根據模型名稱動態分配專屬參數，避免參數不相容導致報錯"""
    if model_name == "nvidia/nemotron-3-ultra-550b-a55b":
        # 只有 Ultra 版本需要 reasoning 參數
        return {
            "extra_body": {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 8192},
            "max_tokens": 16384 # Ultra 支援極大輸出
        }
    
    # 其他一般 instruct 模型，帶入標準安全參數
    return {
        "max_tokens": 4096 # 一般模型建議設為標準安全值，避免超出上限報錯
    }

# ── NVIDIA API（自動 fallback）─────────────────────────────
# 修改 call_nvidia 函數中的 payload 組裝邏輯
async def call_nvidia(system_prompt: str, user_content: str) -> tuple[str, str]:
    """回傳 (回應文字, 使用的模型名稱)，自動嘗試備用模型"""
    last_error = None
    async with httpx.AsyncClient(timeout=120) as client:
        for model in NVIDIA_MODELS:
            try:
                # 1. 取得該模型的專屬設定
                kwargs = get_model_kwargs(model)
                
                # 2. 建立基礎 Payload
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_content},
                    ],
                    "temperature": 0.2,
                }
                
                # 3. 將專屬設定 (max_tokens, extra_body) 融入 Payload
                payload.update(kwargs)

                r = await client.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {NVIDIA_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json=payload, # 使用動態組裝的 payload
                )
                if r.status_code == 200:
                    logger.info(f"使用模型：{model}")
                    return r.json()["choices"][0]["message"]["content"], model
                else:
                    logger.warning(f"模型 {model} 回傳 {r.status_code}：{r.text[:200]}")
                    last_error = f"{r.status_code} {r.text[:200]}"
            except Exception as e:
                logger.warning(f"模型 {model} 發生例外：{e}")
                last_error = str(e)

    raise RuntimeError(
        f"所有模型均失敗。最後錯誤：{last_error}\n\n"
        "請至 https://build.nvidia.com 確認 API Key 有效且有剩餘額度。"
    )

SYSTEM_PROMPT = """你是頂級 Python 程式碼優化專家 Agent。
收到 .py 程式碼與需求後，輸出以下格式，分隔符一字不差：

---OPTIMIZED_CODE---
(完整優化後的 Python 程式碼，純文字，不含 ``` fence)
---END_CODE---

---OPTIMIZATION_REPORT---
## 優化摘要
(整體說明)

## 主要改動
- 改動：原因

## 效能預估
(與原版比較)
---END_REPORT---
"""

async def run_agent(code: str, prompt: str) -> tuple[str, str, str]:
    """回傳 (優化程式碼, 報告, 模型名稱)"""
    raw, model = await call_nvidia(
        SYSTEM_PROMPT,
        f"## 需求\n{prompt}\n\n## 原始程式碼\n```python\n{code}\n```"
    )
    code_out = report_out = raw
    if "---OPTIMIZED_CODE---" in raw and "---END_CODE---" in raw:
        code_out = raw.split("---OPTIMIZED_CODE---")[1].split("---END_CODE---")[0].strip()
    if "---OPTIMIZATION_REPORT---" in raw and "---END_REPORT---" in raw:
        report_out = raw.split("---OPTIMIZATION_REPORT---")[1].split("---END_REPORT---")[0].strip()
    return code_out, report_out, model

# ── Handlers ───────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Python 程式優化 Agent*\n\n"
        "1️⃣ 傳送 `.py` 檔案\n"
        "2️⃣ 輸入優化需求\n"
        "3️⃣ 收到優化檔案與報告\n\n/cancel 取消",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def receive_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc: Document = update.message.document
    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("⚠️ 請上傳 .py 檔案。")
        return WAIT_FILE
    buf = io.BytesIO()
    await (await doc.get_file()).download_to_memory(buf)
    code_str = buf.getvalue().decode("utf-8", errors="replace")
    ctx.user_data.update({"code": code_str, "filename": doc.file_name})
    await update.message.reply_text(
        f"✅ 收到 `{doc.file_name}`（{len(code_str)} 字元）\n\n請輸入優化需求：",
        parse_mode="Markdown"
    )
    return WAIT_PROMPT

# ── 1. 新增：獨立的背景處理函數 ─────────────────────────────
async def process_ai_task(chat_id: int, message_id: int, code: str, prompt_text: str, fname: str, bot):
    """
    這是一個背景任務。
    它會在不阻塞 Telegram Webhook 的情況下，默默在背景呼叫 NVIDIA API。
    """
    try:
        # 呼叫 AI (這裡可能會耗時 1~2 分鐘，但沒關係，已經在背景了)
        opt_code, report, model = await run_agent(code, prompt_text)

        # 執行成功，刪除原本「分析中...」的過渡訊息
        await bot.delete_message(chat_id=chat_id, message_id=message_id)

        # 準備並傳送優化後的檔案
        import io
        buf = io.BytesIO(opt_code.encode())
        out_name = fname.replace(".py", "_optimized.py")
        buf.name = out_name
        
        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            filename=out_name,
            caption=f"✅ 完成！（模型：`{model}`）報告 👇",
            parse_mode="Markdown"
        )
        
        # 傳送報告內容 (處理超長文字分段)
        for i in range(0, len(report), 4000):
            await bot.send_message(chat_id=chat_id, text=report[i:i+4000], parse_mode="Markdown")

    except Exception as e:
        logger.exception("背景 run_agent 失敗")
        # 如果發生錯誤，編輯原本的訊息來通知使用者
        await bot.edit_message_text(f"❌ 發生錯誤，處理中斷：{e}", chat_id=chat_id, message_id=message_id)


# ── 2. 改寫：接收需求的 Handler ─────────────────────────────
async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code  = ctx.user_data.get("code", "")
    fname = ctx.user_data.get("filename", "code.py")
    prompt_text = update.message.text.strip()

    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE
    
    # 步驟 A：先傳送「等待訊息」，並取得該訊息的 ID
    msg = await update.message.reply_text("⚙️ 收到需求！已排入背景運算，這可能需要 1~2 分鐘，請耐心等候...\n(您可以繼續進行其他操作)")
    
    # 步驟 B：建立背景任務 (Fire and Forget 射後不理機制)
    # 利用 asyncio.create_task 把沉重的 AI 請求丟到背景執行
    asyncio.create_task(
        process_ai_task(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            code=code,
            prompt_text=prompt_text,
            fname=fname,
            bot=ctx.bot # 必須把 bot 實例傳進去，背景任務才能發送訊息
        )
    )
    
    # 步驟 C：立刻清理狀態並結束對話
    # 這樣函數就會在幾毫秒內結束，Telegram 就會立刻收到 HTTP 200 OK，不再重試！
    ctx.user_data.clear()
    return ConversationHandler.END

# ── 主程式 ─────────────────────────────────────────────────
def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, receive_file)],
        states={
            WAIT_FILE:   [MessageHandler(filters.Document.ALL, receive_file)],
            WAIT_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  start))
    app.add_handler(conv)

    webhook_url = f"{RENDER_URL}/webhook/{TELEGRAM_TOKEN}"
    logger.info(f"啟動 webhook server，port {PORT}")
    logger.info(f"Webhook URL: {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
        url_path=f"/webhook/{TELEGRAM_TOKEN}",
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == "__main__":
    main()
