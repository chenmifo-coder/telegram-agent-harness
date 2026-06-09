"""
Telegram Agent Harness — 雲端 Python 程式優化機器人
部署平台: Render Free Web Service
啟動方式: gunicorn bot:flask_app
"""

import os, io, asyncio, logging, threading
from flask import Flask, request
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

# ── 環境變數 ────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY  = os.environ["NVIDIA_API_KEY"]
RENDER_URL      = os.environ["RENDER_URL"].rstrip("/")
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL    = "meta/llama-3.1-405b-instruct"

WAIT_FILE, WAIT_PROMPT = range(2)

# ── 獨立 Event Loop（在背景執行緒中持續運行）──────────────
_loop = asyncio.new_event_loop()

def _start_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=_start_loop, args=(_loop,), daemon=True).start()

def run_async(coro):
    """在背景 event loop 執行 coroutine，同步等待結果。"""
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return fut.result(timeout=180)


# ── NVIDIA API ──────────────────────────────────────────────
async def call_nvidia(system_prompt: str, user_content: str) -> str:
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{NVIDIA_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── Agent Harness ───────────────────────────────────────────
SYSTEM_PROMPT = """你是頂級 Python 程式碼優化專家 Agent。
收到 .py 程式碼與需求後，必須輸出以下格式，不得省略分隔符：

---OPTIMIZED_CODE---
(完整優化後的 Python 程式碼，純文字，不含 ``` fence)
---END_CODE---

---OPTIMIZATION_REPORT---
## 優化摘要
(整體說明)

## 主要改動
- 改動項目：原因

## 效能預估
(與原版比較)
---END_REPORT---
"""

async def run_agent(code: str, prompt: str) -> tuple[str, str]:
    user_msg = f"## 需求\n{prompt}\n\n## 原始程式碼\n```python\n{code}\n```"
    raw = await call_nvidia(SYSTEM_PROMPT, user_msg)

    code_out = report_out = raw
    if "---OPTIMIZED_CODE---" in raw and "---END_CODE---" in raw:
        code_out = raw.split("---OPTIMIZED_CODE---")[1].split("---END_CODE---")[0].strip()
    if "---OPTIMIZATION_REPORT---" in raw and "---END_REPORT---" in raw:
        report_out = raw.split("---OPTIMIZATION_REPORT---")[1].split("---END_REPORT---")[0].strip()

    return code_out, report_out


# ── Telegram Handlers ───────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Python 程式優化 Agent*\n\n"
        "1️⃣ 傳送 `.py` 檔案\n"
        "2️⃣ 輸入優化需求\n"
        "3️⃣ 收到優化檔案與報告\n\n"
        "/cancel 取消",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def receive_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc: Document = update.message.document
    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("⚠️ 請上傳 .py 檔案。")
        return WAIT_FILE

    tg_file = await doc.get_file()
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    raw_bytes = buf.getvalue()

    code_str = raw_bytes.decode("utf-8", errors="replace")
    ctx.user_data["code"]     = code_str
    ctx.user_data["filename"] = doc.file_name

    await update.message.reply_text(
        f"✅ 收到 `{doc.file_name}`（{len(code_str)} 字元）\n\n"
        "請輸入優化需求，例如：\n"
        "• 提升效能，改用 async\n"
        "• 加型別標注與 docstring\n"
        "• 修正所有 bug",
        parse_mode="Markdown"
    )
    return WAIT_PROMPT


async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text.strip()
    code   = ctx.user_data.get("code", "")
    fname  = ctx.user_data.get("filename", "code.py")

    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE

    msg = await update.message.reply_text("⚙️ Agent 分析中，請稍候…（約 20-40 秒）")

    try:
        opt_code, report = await run_agent(code, prompt)
    except Exception as e:
        logger.exception("run_agent 失敗")
        await msg.edit_text(f"❌ 錯誤：{e}")
        return ConversationHandler.END

    await msg.delete()

    out_name = fname.replace(".py", "_optimized.py")
    buf = io.BytesIO(opt_code.encode("utf-8"))
    buf.name = out_name
    await update.message.reply_document(
        document=buf, filename=out_name,
        caption="✅ 優化完成！報告如下 👇"
    )

    for i in range(0, len(report), 4000):
        await update.message.reply_text(report[i:i+4000], parse_mode="Markdown")

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("🚫 已取消。")
    return ConversationHandler.END


# ── 建立 Application，初始化後設定 Webhook ─────────────────
async def _build_and_init() -> Application:
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

    await app.initialize()   # 必須呼叫，才能使用 bot

    webhook_url = f"{RENDER_URL}/webhook/{TELEGRAM_TOKEN}"
    await app.bot.set_webhook(
        url=webhook_url,
        allowed_updates=["message"],
        drop_pending_updates=True,
    )
    logger.info(f"Webhook 設定完成：{webhook_url}")
    return app


# 同步初始化，在背景 loop 執行
tg_app: Application = run_async(_build_and_init())


# ── Flask App ───────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    data = request.get_json(force=True)
    if not data:
        return "bad request", 400
    update = Update.de_json(data, tg_app.bot)
    run_async(tg_app.process_update(update))
    return "ok", 200

@flask_app.route("/health")
def health():
    return "ok", 200

@flask_app.route("/")
def index():
    return "Agent Harness running", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)
