import os
import io
import logging
import asyncio
from typing import Tuple, Dict, Any
import httpx
from telegram import Update, Document
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# -------------------------- 配置與常數 --------------------------
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY = os.environ["NVIDIA_API_KEY"]
RENDER_URL = os.environ["RENDER_URL"].rstrip("/")
PORT = int(os.environ.get("PORT", 10000))

if not NVIDIA_API_KEY:
    raise ValueError("Missing NVIDIA_API_KEY – check Render Environment variables.")

# 模型 fallback 列表（依優先順序）
NVIDIA_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
]

# 對話狀態
WAIT_FILE, WAIT_PROMPT = range(2)

# 檔案大小上限（字元數），約 120k 字元對應 ~30k Tokens，安全上限
MAX_FILE_CHARS = 120_000
# AI 呼叫逾時（秒）
AI_REQUEST_TIMEOUT = 300
# 同時進行的 AI 請求數上限，防止併發過高
MAX_CONCURRENT_AI = 2
# 日誌格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# -------------------------- 模型參數映射 --------------------------
class ModelConfig:
    """針對特定模型的專屬參數"""
    def __init__(self, max_tokens: int, extra_body: Dict[str, Any] | None = None):
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}

MODEL_KWARGS: Dict[str, ModelConfig] = {
    "nvidia/nemotron-3-ultra-550b-a55b": ModelConfig(
        max_tokens=16384,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 8192},
    ),
    # 其他模型使用共通設定
    "default": ModelConfig(max_tokens=4096),
}

def get_model_kwargs(model_name: str) -> Dict[str, Any]:
    cfg = MODEL_KWARGS.get(model_name, MODEL_KWARGS["default"])
    payload: Dict[str, Any] = {"max_tokens": cfg.max_tokens}
    if cfg.extra_body:
        payload["extra_body"] = cfg.extra_body
    return payload

# -------------------------- 全域 HTTP 客戶端 --------------------------
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()

async def get_http_client() -> httpx.AsyncClient:
    """惰性建立並回傳共用的 AsyncClient（含 timeout、連線池限制）"""
    global _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=AI_REQUEST_TIMEOUT,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return _http_client

async def close_http_client():
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None

# -------------------------- NVIDIA API 呼叫（含 fallback） --------------------------
async def call_nvidia(system_prompt: str, user_content: str) -> Tuple[str, str]:
    """
    嘗試使用 NVIDIA_MODELS 列表中的模型，回傳 (回應文字, 使用的模型名稱)。
    所有模型失敗時拋出 RuntimeError。
    """
    last_error: str | None = None
    client = await get_http_client()

    for model in NVIDIA_MODELS:
        try:
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                "temperature": 0.2,
            }
            payload.update(get_model_kwargs(model))

            resp = await client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {NVIDIA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code == 200:
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(f"NVIDIA API 成功使用模型：{model}")
                return content, model
            else:
                err_msg = f"{resp.status_code} {resp.text[:200]}"
                logger.warning(f"模型 {model} 回傳錯誤：{err_msg}")
                last_error = err_msg
        except Exception as exc:  # 包含網路、逾時等
            logger.warning(f"模型 {model} 呼叫例外：{exc}")
            last_error = str(exc)

    raise RuntimeError(
        f"所有 NVIDIA 模型均失敗。最後錯誤：{last_error}\n"
        "請檢查 API Key 是否有效、額度是否足夠，或稍後再試。"
    )

# -------------------------- 常數 Prompt --------------------------
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

# --- 關閉 httpx 底層連線的 INFO 廣播 ---
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

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
# ── 3. 新增：取消操作的 Handler ─────────────────────────────
async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """使用者輸入 /cancel 時觸發，清除狀態並結束對話"""
    ctx.user_data.clear()
    await update.message.reply_text("🚫 操作已取消。您可以隨時重新上傳檔案。")
    return ConversationHandler.END

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
    
    # --- 新增：檔案大小限制檢查 ---
    # 設定一個合理的上限，例如 12,000 字元 (約 3000~4000 Tokens)
    MAX_CHARS = 120000
    if len(code_str) > MAX_CHARS:
        await update.message.reply_text(
            f"⚠️ 檔案太大了！(目前字元數：{len(code_str)})\n"
            f"為了確保 AI 能夠穩定處理，請上傳小於 {MAX_CHARS} 字元的程式碼檔案。\n"
            "您可嘗試將程式碼拆分為多個小檔案後再上傳。"
        )
        return WAIT_FILE # 讓使用者重新上傳

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
# --- 新增：製作遮蔽版的 Token 以供安全 Log 顯示 ---
    masked_token = f"{TELEGRAM_TOKEN[:5]}...[隱藏]...{TELEGRAM_TOKEN[-5:]}" if len(TELEGRAM_TOKEN) > 10 else "***"
    masked_webhook_url = f"{RENDER_URL}/webhook/{masked_token}"
    
    logger.info(f"啟動 webhook server，port {PORT}")
    logger.info(f"Webhook URL: {masked_webhook_url}") # 改為印出安全版 URL

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url, # 實際註冊仍使用真實 Token
        url_path=f"/webhook/{TELEGRAM_TOKEN}",
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )

if __name__ == "__main__":
    main()
