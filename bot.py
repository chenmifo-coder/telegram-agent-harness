"""
Telegram Agent Harness — Render Free Web Service
修正：多模型 fallback + 詳細錯誤回報
"""
import os
from openai import OpenAI
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
if not api_key:
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
async def call_nvidia(system_prompt: str, user_content: str) -> tuple[str, str]:
    """回傳 (回應文字, 使用的模型名稱)，自動嘗試備用模型"""
    last_error = None
    async with httpx.AsyncClient(timeout=120) as client:
        for model in NVIDIA_MODELS:
            try:
                r = await client.post(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {NVIDIA_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_content},
                        ],
                        "temperature": 0.2,
                        "max_tokens": 4096,
                    },
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

async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code  = ctx.user_data.get("code", "")
    fname = ctx.user_data.get("filename", "code.py")
    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE
    msg = await update.message.reply_text("⚙️ 分析中，約 20-40 秒…")
    try:
        opt_code, report, model = await run_agent(code, update.message.text.strip())
    except Exception as e:
        logger.exception("run_agent 失敗")
        await msg.edit_text(f"❌ 錯誤：{e}")
        return ConversationHandler.END
    await msg.delete()
    buf = io.BytesIO(opt_code.encode())
    out_name = fname.replace(".py", "_optimized.py")
    buf.name = out_name
    await update.message.reply_document(
        document=buf, filename=out_name,
        caption=f"✅ 完成！（模型：`{model}`）報告 👇",
        parse_mode="Markdown"
    )
    for i in range(0, len(report), 4000):
        await update.message.reply_text(report[i:i+4000], parse_mode="Markdown")
    ctx.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("🚫 已取消。")
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
