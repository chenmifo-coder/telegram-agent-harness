"""
Telegram Agent Harness — 雲端 Python 程式優化機器人
部署平台: Render (免費方案)
LLM: NVIDIA API (llama-3.1-405b-instruct)
"""

import os
import io
import asyncio
import logging
from telegram import Update, Document
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 環境變數 ────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY  = os.environ["NVIDIA_API_KEY"]
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL    = "meta/llama-3.1-405b-instruct"   # 可換 mistralai/mixtral-8x22b-instruct

# ConversationHandler 狀態
WAIT_FILE, WAIT_PROMPT = range(2)

# ── NVIDIA API 呼叫 ─────────────────────────────────────────
async def call_nvidia(system_prompt: str, user_content: str) -> str:
    """呼叫 NVIDIA NIM API，回傳模型回應文字。"""
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.2,
        "top_p": 0.95,
        "max_tokens": 4096,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{NVIDIA_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ── Agent Harness ───────────────────────────────────────────
SYSTEM_PROMPT = """你是頂級 Python 程式碼優化專家 Agent。
收到使用者的 .py 程式碼與需求描述後，你必須：

1. **分析**：找出效能瓶頸、程式碼異味、潛在 bug、可讀性問題。
2. **優化**：依需求描述改寫程式碼，包含：
   - 效能優化（演算法複雜度、async/await、快取）
   - 可讀性改善（type hints、docstring、命名）
   - 安全性修正
   - 依賴最小化
3. **回報**：輸出格式必須嚴格遵守：

---OPTIMIZED_CODE---
(完整優化後的 Python 程式碼，不含 markdown fence)
---END_CODE---

---OPTIMIZATION_REPORT---
## 優化摘要
(2-3 句整體說明)

## 主要改動
- 改動 1：原因
- 改動 2：原因
...

## 效能預估
(與原版比較的預期改善)

## 使用方式
(如有 API 變更，說明新用法)
---END_REPORT---
"""

async def run_agent(code: str, prompt: str) -> tuple[str, str]:
    """執行 Agent Harness，回傳 (優化程式碼, 優化報告)。"""
    user_content = f"""## 需求描述
{prompt}

## 原始程式碼
```python
{code}
```
"""
    raw = await call_nvidia(SYSTEM_PROMPT, user_content)

    # 解析輸出
    code_out, report_out = raw, raw
    if "---OPTIMIZED_CODE---" in raw and "---END_CODE---" in raw:
        code_out = raw.split("---OPTIMIZED_CODE---")[1].split("---END_CODE---")[0].strip()
    if "---OPTIMIZATION_REPORT---" in raw and "---END_REPORT---" in raw:
        report_out = raw.split("---OPTIMIZATION_REPORT---")[1].split("---END_REPORT---")[0].strip()

    return code_out, report_out


# ── Telegram Handlers ───────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Python 程式優化 Agent*\n\n"
        "使用方式：\n"
        "1️⃣ 傳送你的 `.py` 檔案\n"
        "2️⃣ 輸入優化需求（中英文皆可）\n"
        "3️⃣ 等待優化後的檔案與報告\n\n"
        "輸入 /cancel 可取消當前操作。",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def receive_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    doc: Document = update.message.document
    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("⚠️ 請上傳 .py 檔案。")
        return WAIT_FILE

    # 下載檔案內容
    tg_file = await doc.get_file()
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    code_bytes = buf.getvalue()

    try:
        code_str = code_bytes.decode("utf-8")
    except UnicodeDecodeError:
        code_str = code_bytes.decode("latin-1")

    ctx.user_data["code"]      = code_str
    ctx.user_data["filename"]  = doc.file_name

    await update.message.reply_text(
        f"✅ 已收到 `{doc.file_name}`（{len(code_str)} 字元）\n\n"
        "請輸入**優化需求**，例如：\n"
        "• 提升效能，改用 async\n"
        "• 加上型別標注與 docstring\n"
        "• 修正所有潛在 bug",
        parse_mode="Markdown"
    )
    return WAIT_PROMPT


async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    prompt = update.message.text.strip()
    code   = ctx.user_data.get("code", "")
    fname  = ctx.user_data.get("filename", "optimized.py")

    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE

    thinking_msg = await update.message.reply_text(
        "⚙️ Agent 分析中，請稍候…（約 15-40 秒）"
    )

    try:
        opt_code, report = await run_agent(code, prompt)
    except Exception as e:
        logger.exception("Agent 執行失敗")
        await thinking_msg.edit_text(f"❌ 發生錯誤：{e}")
        return ConversationHandler.END

    await thinking_msg.delete()

    # 回傳優化後 .py
    opt_fname = fname.replace(".py", "_optimized.py")
    code_io   = io.BytesIO(opt_code.encode("utf-8"))
    code_io.name = opt_fname
    await update.message.reply_document(
        document=code_io,
        filename=opt_fname,
        caption="✅ 優化完成！以下是報告 👇",
    )

    # 回傳文字報告（超過 4096 字自動切割）
    max_len = 4000
    for i in range(0, len(report), max_len):
        await update.message.reply_text(
            report[i : i + max_len],
            parse_mode="Markdown"
        )

    ctx.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("🚫 已取消。隨時可重新上傳檔案。")
    return ConversationHandler.END


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Update 發生錯誤", exc_info=ctx.error)


# ── 主程式 ──────────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Document.ALL, receive_file)],
        states={
            WAIT_FILE:   [MessageHandler(filters.Document.ALL, receive_file)],
            WAIT_PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help",  start))
    app.add_handler(conv)
    app.add_error_handler(error_handler)

    logger.info("Bot 啟動中...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
