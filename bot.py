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

# ─────────────────────────── 配置與常數 ───────────────────────────
TELEGRAM_TOKEN: str = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY: str = os.environ["NVIDIA_API_KEY"]
RENDER_URL: str     = os.environ["RENDER_URL"].rstrip("/")
PORT: int           = int(os.environ.get("PORT", 10000))

if not NVIDIA_API_KEY:
    raise ValueError("Missing NVIDIA_API_KEY – check Render Environment variables.")

# 模型 fallback 列表（依優先順序）
NVIDIA_MODELS: list[str] = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
]

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# 對話狀態
WAIT_FILE, WAIT_PROMPT = range(2)

# 字元上限（~120k 字元 ≈ 30k Tokens）
MAX_FILE_CHARS = 120_000
# AI 呼叫逾時（秒）
AI_REQUEST_TIMEOUT = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# ─────────────────────────── 模型參數映射 ───────────────────────────
class ModelConfig:
    """針對特定模型的專屬參數。"""
    __slots__ = ("max_tokens", "extra_body")

    def __init__(self, max_tokens: int, extra_body: Dict[str, Any] | None = None):
        self.max_tokens = max_tokens
        self.extra_body: Dict[str, Any] = extra_body or {}

MODEL_KWARGS: Dict[str, ModelConfig] = {
    "nvidia/nemotron-3-ultra-550b-a55b": ModelConfig(
        max_tokens=16384,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": 8192,
        },
    ),
    "default": ModelConfig(max_tokens=4096),
}

def get_model_payload_extras(model_name: str) -> Dict[str, Any]:
    """回傳要合併進請求 payload 的額外欄位。"""
    cfg = MODEL_KWARGS.get(model_name, MODEL_KWARGS["default"])
    payload: Dict[str, Any] = {"max_tokens": cfg.max_tokens}
    if cfg.extra_body:
        payload["extra_body"] = cfg.extra_body
    return payload

# ─────────────────────────── 全域 HTTP 客戶端 ───────────────────────────
_http_client: httpx.AsyncClient | None = None
_http_client_lock = asyncio.Lock()

async def get_http_client() -> httpx.AsyncClient:
    """惰性建立並回傳共用的 AsyncClient（含 timeout、連線池限制）。"""
    global _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                timeout=AI_REQUEST_TIMEOUT,
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
    return _http_client

async def close_http_client() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None

# ─────────────────────────── NVIDIA API（含 fallback） ───────────────────────────
async def call_nvidia(system_prompt: str, user_content: str) -> Tuple[str, str]:
    """
    依序嘗試 NVIDIA_MODELS 列表中的模型。
    回傳 (回應文字, 使用的模型名稱)；全部失敗則拋出 RuntimeError。
    """
    client = await get_http_client()
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Content-Type": "application/json",
    }
    last_error: str = "（未發出任何請求）"

    for model in NVIDIA_MODELS:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            "temperature": 0.2,
            **get_model_payload_extras(model),
        }
        try:
            resp = await client.post(NVIDIA_API_URL, headers=headers, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                content: str = data["choices"][0]["message"]["content"]
                logger.info("NVIDIA API 成功使用模型：%s", model)
                return content, model

            last_error = f"{resp.status_code} {resp.text[:200]}"
            logger.warning("模型 %s 回傳錯誤：%s", model, last_error)

        except Exception as exc:
            last_error = str(exc)
            logger.warning("模型 %s 呼叫例外：%s", model, last_error)

    raise RuntimeError(
        f"所有 NVIDIA 模型均失敗。最後錯誤：{last_error}\n"
        "請檢查 API Key 是否有效、額度是否足夠，或稍後再試。"
    )

# ─────────────────────────── System Prompt ───────────────────────────
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

_SEP_CODE_START  = "---OPTIMIZED_CODE---"
_SEP_CODE_END    = "---END_CODE---"
_SEP_REPORT_START = "---OPTIMIZATION_REPORT---"
_SEP_REPORT_END  = "---END_REPORT---"

def _parse_agent_response(raw: str) -> Tuple[str, str]:
    """從 AI 回應中解析出優化程式碼與報告；解析失敗則回傳原始文字。"""
    code = raw
    report = raw

    if _SEP_CODE_START in raw and _SEP_CODE_END in raw:
        code = raw.split(_SEP_CODE_START, 1)[1].split(_SEP_CODE_END, 1)[0].strip()

    if _SEP_REPORT_START in raw and _SEP_REPORT_END in raw:
        report = raw.split(_SEP_REPORT_START, 1)[1].split(_SEP_REPORT_END, 1)[0].strip()

    return code, report

async def run_agent(code: str, prompt: str) -> Tuple[str, str, str]:
    """回傳 (優化程式碼, 報告, 模型名稱)。"""
    user_content = f"## 需求\n{prompt}\n\n## 原始程式碼\n```python\n{code}\n```"
    raw, model = await call_nvidia(SYSTEM_PROMPT, user_content)
    code_out, report_out = _parse_agent_response(raw)
    return code_out, report_out, model

# ─────────────────────────── Telegram Handlers ───────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "👋 *Python 程式優化 Agent*\n\n"
        "1️⃣ 傳送 `.py` 檔案\n"
        "2️⃣ 輸入優化需求\n"
        "3️⃣ 收到優化檔案與報告\n\n/cancel 取消",
        parse_mode="Markdown",
    )
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text("🚫 操作已取消。您可以隨時重新上傳檔案。")
    return ConversationHandler.END

async def receive_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    doc: Document = update.message.document

    if not doc.file_name.endswith(".py"):
        await update.message.reply_text("⚠️ 請上傳 .py 檔案。")
        return WAIT_FILE

    buf = io.BytesIO()
    tg_file = await doc.get_file()
    await tg_file.download_to_memory(buf)
    code_str = buf.getvalue().decode("utf-8", errors="replace")

    if len(code_str) > MAX_FILE_CHARS:
        await update.message.reply_text(
            f"⚠️ 檔案太大了！（目前字元數：{len(code_str):,}）\n"
            f"請上傳小於 {MAX_FILE_CHARS:,} 字元的程式碼檔案，\n"
            "或將程式碼拆分為多個小檔案後再上傳。"
        )
        return WAIT_FILE

    ctx.user_data.update({"code": code_str, "filename": doc.file_name})
    await update.message.reply_text(
        f"✅ 收到 `{doc.file_name}`（{len(code_str):,} 字元）\n\n請輸入優化需求：",
        parse_mode="Markdown",
    )
    return WAIT_PROMPT

async def _process_ai_task(
    chat_id: int,
    message_id: int,
    code: str,
    prompt_text: str,
    fname: str,
    bot,
) -> None:
    """背景工作：呼叫 AI 並將結果傳回 Telegram。"""
    try:
        opt_code, report, model = await run_agent(code, prompt_text)

        await bot.delete_message(chat_id=chat_id, message_id=message_id)

        out_name = fname.removesuffix(".py") + "_optimized.py"
        buf = io.BytesIO(opt_code.encode())
        buf.name = out_name

        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            filename=out_name,
            caption=f"✅ 完成！（模型：`{model}`）報告 👇",
            parse_mode="Markdown",
        )

        # 分段傳送報告（Telegram 單訊息上限 4096 字元）
        for i in range(0, len(report), 4000):
            await bot.send_message(
                chat_id=chat_id,
                text=report[i : i + 4000],
                parse_mode="Markdown",
            )

    except Exception:
        logger.exception("背景 run_agent 失敗（chat_id=%s）", chat_id)
        await bot.edit_message_text(
            "❌ 發生錯誤，處理中斷。請稍後再試或聯絡管理員。",
            chat_id=chat_id,
            message_id=message_id,
        )

async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    code       = ctx.user_data.get("code", "")
    fname      = ctx.user_data.get("filename", "code.py")
    prompt_text = update.message.text.strip()

    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE

    msg = await update.message.reply_text(
        "⚙️ 收到需求！已排入背景運算，這可能需要 1~2 分鐘，請耐心等候…\n"
        "（您可以繼續進行其他操作）"
    )

    asyncio.create_task(
        _process_ai_task(
            chat_id=update.effective_chat.id,
            message_id=msg.message_id,
            code=code,
            prompt_text=prompt_text,
            fname=fname,
            bot=ctx.bot,
        )
    )

    ctx.user_data.clear()
    return ConversationHandler.END

# ─────────────────────────── 主程式 ───────────────────────────
def main() -> None:
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

    # 遮蔽 Token，只記錄安全版本的 URL
    masked_token = (
        f"{TELEGRAM_TOKEN[:5]}...[隱藏]...{TELEGRAM_TOKEN[-5:]}"
        if len(TELEGRAM_TOKEN) > 10 else "***"
    )
    logger.info("啟動 webhook server，port %d", PORT)
    logger.info("Webhook URL: %s/webhook/%s", RENDER_URL, masked_token)

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
