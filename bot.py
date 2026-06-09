"""
Telegram Agent Harness — Render Free Web Service
架構：python-telegram-bot 內建 webhook server (基於 tornado)
完全不需要 gunicorn / flask / threading
"""
import os, io, logging, asyncio
from telegram import Update, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY  = os.environ["NVIDIA_API_KEY"]
RENDER_URL      = os.environ["RENDER_URL"].rstrip("/")
NVIDIA_MODEL    = "meta/llama-3.1-405b-instruct"
PORT            = int(os.environ.get("PORT", 10000))

WAIT_FILE, WAIT_PROMPT = range(2)

# ── NVIDIA API ─────────────────────────────────────────────
async def call_nvidia(system_prompt: str, user_content: str) -> str:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            json={
                "model": NVIDIA_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

SYSTEM_PROMPT = """你是頂級 Python 程式碼優化專家 Agent。
收到 .py 程式碼與需求後，必須輸出以下格式，分隔符一字不差：

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

async def run_agent(code: str, prompt: str) -> tuple[str, str]:
    raw = await call_nvidia(
        SYSTEM_PROMPT,
        f"## 需求\n{prompt}\n\n## 原始程式碼\n```python\n{code}\n```"
    )
    code_out = report_out = raw
    if "---OPTIMIZED_CODE---" in raw and "---END_CODE---" in raw:
        code_out = raw.split("---OPTIMIZED_CODE---")[1].split("---END_CODE---")[0].strip()
    if "---OPTIMIZATION_REPORT---" in raw and "---END_REPORT---" in raw:
        report_out = raw.split("---OPTIMIZATION_REPORT---")[1].split("---END_REPORT---")[0].strip()
    return code_out, report_out

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

async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code  = ctx.user_data.get("code", "")
    fname = ctx.user_data.get("filename", "code.py")
    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE
    msg = await update.message.reply_text("⚙️ 分析中，約 20-40 秒…")
    try:
        opt_code, report = await run_agent(code, update.message.text.strip())
    except Exception as e:
        logger.exception("run_agent 失敗")
        await msg.edit_text(f"❌ 錯誤：{e}")
        return ConversationHandler.END
    await msg.delete()
    buf = io.BytesIO(opt_code.encode())
    out_name = fname.replace(".py", "_optimized.py")
    buf.name = out_name
    await update.message.reply_document(document=buf, filename=out_name, caption="✅ 完成！報告 👇")
    for i in range(0, len(report), 4000):
        await update.message.reply_text(report[i:i+4000], parse_mode="Markdown")
    ctx.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("🚫 已取消。")
    return ConversationHandler.END

# ── 主程式：使用 PTB 內建 webhook server ──────────────────
def main():
    # Python 3.14 不再自動建立 event loop，需手動設定
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
    logger.info(f"啟動 webhook server，監聽 port {PORT}")
    logger.info(f"Webhook URL: {webhook_url}")

    # run_webhook 會自行啟動 tornado HTTP server，設定 webhook，並阻塞直到結束
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
        url_path=f"/webhook/{TELEGRAM_TOKEN}",
        drop_pending_updates=True,
        # 健康檢查端點：讓 Render 的 HEAD / 能收到 200
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == "__main__":
    main()
