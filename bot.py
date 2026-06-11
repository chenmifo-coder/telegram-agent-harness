import os
import io
import html
import logging
import asyncio
import time
from typing import Tuple, Dict, Optional, List, Final

from openai import AsyncOpenAI, RateLimitError, AuthenticationError, APIStatusError
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
TELEGRAM_TOKEN: Final[str] = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY: Final[str] = os.environ["NVIDIA_API_KEY"]
RENDER_URL: Final[str] = os.environ["RENDER_URL"].rstrip("/")
PORT: Final[int] = int(os.environ.get("PORT", 10000))

if not NVIDIA_API_KEY:
    raise ValueError("Missing NVIDIA_API_KEY – check Render Environment variables.")

# 模型 fallback 列表（依優先順序）
NVIDIA_MODELS: Final[List[str]] = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",
    "nvidia/llama-3.3-nemotron-super-49b-v1",
    "meta/llama-3.3-70b-instruct",
    "meta/llama-3.1-8b-instruct",
]

# 對話狀態
WAIT_FILE, WAIT_PROMPT = range(2)

# 字元上限（~120k 字元 ≈ 30k Tokens）
MAX_FILE_CHARS: Final[int] = 120_000
# Telegram 訊息分段上限
TG_MSG_LIMIT: Final[int] = 4000

# ── Rate Limit 控制 ──────────────────────────────────────────────
RPM_LIMIT: Final[int] = 40
RPM_MIN_INTERVAL: Final[float] = 60.0 / RPM_LIMIT          # 1.5 秒
RPM_MAX_BACKOFF: Final[float] = 60.0
RPM_MAX_RETRIES: Final[int] = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# ─────────────────────────── 模型參數映射 ───────────────────────────
class ModelConfig:
    __slots__ = ("max_tokens", "thinking_budget", "temperature", "top_p")
    def __init__(
        self,
        max_tokens: int,
        thinking_budget: Optional[int] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
    ):
        self.max_tokens = max_tokens
        self.thinking_budget = thinking_budget
        self.temperature = temperature
        self.top_p = top_p

    @property
    def extra_body(self) -> Optional[Dict]:
        """根據 thinking_budget 產生 extra_body，若無則回傳 None（讓 API 使用預設行為）。"""
        if self.thinking_budget is not None:
            # 注意：當我們明確設定 thinking_budget 時，通常希望啟用 thinking，
            # 但若希望強制關閉 thinking，可以將 enable_thinking 設為 False，
            # 並省略 reasoning_budget 或設為 0。此處保留彈性：若 budget > 0 則開啟 thinking。
            if self.thinking_budget > 0:
                return {
                    "chat_template_kwargs": {"enable_thinking": True},
                    "reasoning_budget": self.thinking_budget,
                }
            else:
                # budget 為 0 或負數時，關閉 thinking
                return {"chat_template_kwargs": {"enable_thinking": False}}
        return None  # 不傳 extra_body，讓模型使用預設行為

MODEL_CONFIGS: Final[Dict[str, ModelConfig]] = {
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": ModelConfig(
        max_tokens=16384, thinking_budget=16384, temperature=1.0, top_p=0.95,
    ),
    "nvidia/llama-3.3-nemotron-super-49b-v1": ModelConfig(
        max_tokens=16384, thinking_budget=8192, temperature=1.0, top_p=0.95,
    ),
}
_DEFAULT_CONFIG: Final[ModelConfig] = ModelConfig(max_tokens=16384, temperature=0.2)

# ─────────────────────────── 全域 OpenAI 客戶端 ──────────────────────
_nvidia_client: Optional[AsyncOpenAI] = None

def get_nvidia_client() -> AsyncOpenAI:
    global _nvidia_client
    if _nvidia_client is None:
        _nvidia_client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY,
            timeout=300.0,
            max_retries=0,
        )
    return _nvidia_client

# ─────────────────────────── Rate Limiter ─────────────────────────
class RateLimiter:
    __slots__ = ("_min_interval", "_last_call_at", "_lock")
    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._last_call_at: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_call_at)
            if wait > 0:
                logger.debug("Rate limiter: wait %.2f sec", wait)
                await asyncio.sleep(wait)
            self._last_call_at = time.monotonic()

_rate_limiter = RateLimiter(RPM_MIN_INTERVAL)

# ─────────────────────────── NVIDIA API（streaming + fallback） ───
async def _call_single_model(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_content: str,
) -> str:
    cfg = MODEL_CONFIGS.get(model, _DEFAULT_CONFIG)
    extra_body = cfg.extra_body

    # 重試邏輯：共嘗試 (RPM_MAX_RETRIES + 1) 次
    for attempt in range(RPM_MAX_RETRIES + 1):
        await _rate_limiter.acquire()
        try:
            full_text_parts: List[str] = []
            finish_reason = None
            stream = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                temperature=cfg.temperature,
                top_p=cfg.top_p,
                max_tokens=cfg.max_tokens,
                extra_body=extra_body,
                stream=True,
            )
            async for chunk in stream:
                if not chunk.choices:
                    continue
                finish_reason = chunk.choices[0].finish_reason
                delta = chunk.choices[0].delta
                if delta.content:
                    full_text_parts.append(delta.content)

            full_text = "".join(full_text_parts)
            if finish_reason == "length":
                logger.warning(
                    "Response from %s was truncated due to max_tokens limit (max_tokens=%d). "
                    "Consider increasing the limit or using a model with larger output capacity.",
                    model, cfg.max_tokens
                )
            return full_text

        except RateLimitError as exc:
            if attempt == RPM_MAX_RETRIES:
                raise
            retry_after = RPM_MIN_INTERVAL * (2 ** attempt)
            try:
                header_val = exc.response.headers.get("retry-after")
                if header_val:
                    retry_after = min(float(header_val), RPM_MAX_BACKOFF)
            except Exception:
                pass
            retry_after = min(retry_after, RPM_MAX_BACKOFF)
            logger.warning(
                "Model %s 429 (attempt %d/%d) – wait %.1fs",
                model, attempt + 1, RPM_MAX_RETRIES + 1, retry_after,
            )
            await asyncio.sleep(retry_after)

async def call_nvidia(system_prompt: str, user_content: str) -> Tuple[str, str]:
    client = get_nvidia_client()
    last_error = "No request sent"

    for model in NVIDIA_MODELS:
        try:
            text = await _call_single_model(client, model, system_prompt, user_content)
            logger.info("NVIDIA API succeeded with model: %s", model)
            return text, model
        except AuthenticationError as exc:
            raise RuntimeError(f"NVIDIA API auth failed: {exc}") from exc
        except RateLimitError as exc:
            last_error = f"429 Rate Limited: {exc}"
            logger.warning("Model %s exhausted retries, fallback next", model)
        except APIStatusError as exc:
            last_error = f"HTTP {exc.status_code}: {exc.message[:200]}"
            logger.warning("Model %s API error: %s", model, last_error)
        except Exception as exc:
            last_error = repr(exc)
            logger.warning("Model %s exception: %s", model, last_error)

    raise RuntimeError(
        f"All NVIDIA models failed. Last error: {last_error}\n"
        "Check API key, quota, or try again later."
    )

# ─────────────────────────── System Prompt ───────────────────────────
SYSTEM_PROMPT = """你是頂級 Python 程式碼優化專家 Agent。

【重要規則】
1. 第一個字必須是 ---OPTIMIZED_CODE--- ，前面不得有任何說明、前言或空行。
2. 嚴格按照下列格式輸出，分隔符一字不差，不得在分隔符前後加任何說明文字。
3. 程式碼區塊內只放純 Python 原始碼，不得含 ``` fence 或任何 Markdown 標記。

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

_SEP_CODE_START   = "---OPTIMIZED_CODE---"
_SEP_CODE_END     = "---END_CODE---"
_SEP_REPORT_START = "---OPTIMIZATION_REPORT---"
_SEP_REPORT_END   = "---END_REPORT---"

def _parse_agent_response(raw: str) -> Tuple[str, str]:
    has_code   = _SEP_CODE_START in raw and _SEP_CODE_END in raw
    has_report = _SEP_REPORT_START in raw and _SEP_REPORT_END in raw

    if not has_code:
        logger.warning("Missing CODE delimiters, raw[:200]=%s", raw[:200])
        raise ValueError("AI response missing CODE delimiters")

    code = raw.split(_SEP_CODE_START, 1)[1].split(_SEP_CODE_END, 1)[0].strip()
    if has_report:
        report = raw.split(_SEP_REPORT_START, 1)[1].split(_SEP_REPORT_END, 1)[0].strip()
    else:
        logger.warning("Missing REPORT delimiters")
        report = "（優化報告解析失敗，但程式碼已成功提取）"
    return code, report

async def run_agent(code: str, prompt: str) -> Tuple[str, str, str]:
    user_content = f"## 需求\n{prompt}\n\n## 原始程式碼\n```python\n{code}\n```"
    parse_attempts = 3

    for attempt in range(1, parse_attempts + 1):
        raw, model = await call_nvidia(SYSTEM_PROMPT, user_content)
        try:
            code_out, report_out = _parse_agent_response(raw)
            return code_out, report_out, model
        except ValueError as exc:
            logger.warning("Parse failed (attempt %d/%d): %s", attempt, parse_attempts, exc)
            if attempt == parse_attempts:
                raise RuntimeError(
                    f"AI failed to follow format after {parse_attempts} attempts."
                ) from exc
            await asyncio.sleep(2.0)  # 避免快速重試

    raise RuntimeError("run_agent: unreachable")

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
            f"請上傳小於 {MAX_FILE_CHARS:,} 字元的程式碼檔案。"
        )
        return WAIT_FILE

    ctx.user_data.update({"code": code_str, "filename": doc.file_name})
    await update.message.reply_text(
        f"✅ 收到 {html.escape(doc.file_name)}（{len(code_str):,} 字元）\n\n請輸入優化需求：",
    )
    return WAIT_PROMPT

async def _process_ai_task(
    chat_id: int,
    status_message_id: int,
    code: str,
    prompt_text: str,
    fname: str,
    bot,
) -> None:
    try:
        opt_code, report, model = await run_agent(code, prompt_text)

        out_name = fname.removesuffix(".py") + "_optimized.py"
        buf = io.BytesIO(opt_code.encode("utf-8"))
        buf.name = out_name

        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            filename=out_name,
            caption=f"✅ 完成！（模型：{html.escape(model)}）報告 👇",
        )

        try:
            await bot.delete_message(chat_id=chat_id, message_id=status_message_id)
        except Exception:
            pass

        # 分段傳送報告
        for i in range(0, len(report), TG_MSG_LIMIT):
            await bot.send_message(
                chat_id=chat_id,
                text=report[i:i + TG_MSG_LIMIT],
            )
    except Exception:
        logger.exception("Background agent failed (chat_id=%s)", chat_id)
        try:
            await bot.edit_message_text(
                "❌ 發生錯誤，處理中斷。請稍後再試或聯絡管理員。",
                chat_id=chat_id,
                message_id=status_message_id,
            )
        except Exception:
            await bot.send_message(
                chat_id=chat_id,
                text="❌ 發生錯誤，處理中斷。請稍後再試或聯絡管理員。",
            )

async def receive_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    code = ctx.user_data.get("code", "")
    fname = ctx.user_data.get("filename", "code.py")
    prompt_text = update.message.text.strip()

    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE

    msg = await update.message.reply_text(
        "⚙️ 收到需求！已排入背景運算，這可能需要 1~2 分鐘，請耐心等候…\n"
        "（您可以繼續進行其他操作）"
    )
    ctx.user_data.clear()

    asyncio.create_task(
        _process_ai_task(
            chat_id=update.effective_chat.id,
            status_message_id=msg.message_id,
            code=code,
            prompt_text=prompt_text,
            fname=fname,
            bot=ctx.bot,
        )
    )
    return ConversationHandler.END

# ─────────────────────────── 主程式 ───────────────────────────
def _build_app() -> Application:
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
    app.add_handler(CommandHandler("help", start))
    app.add_handler(conv)
    return app

def main() -> None:
    # 為相容 python-telegram-bot 21.x 手動建立 event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = _build_app()
    webhook_url = f"{RENDER_URL}/webhook/{TELEGRAM_TOKEN}"
    masked_token = (
        f"{TELEGRAM_TOKEN[:5]}...[隱藏]...{TELEGRAM_TOKEN[-5:]}"
        if len(TELEGRAM_TOKEN) > 10 else "***"
    )
    logger.info("Starting webhook on port %d", PORT)
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
