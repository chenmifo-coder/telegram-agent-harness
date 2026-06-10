import os
import io
import logging
import asyncio
import time
from typing import Tuple, Dict, Optional

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

# 對話狀態
WAIT_FILE, WAIT_PROMPT = range(2)

# 字元上限（~120k 字元 ≈ 30k Tokens）
MAX_FILE_CHARS = 120_000
# Telegram 訊息分段上限
TG_MSG_LIMIT = 4000

# ── Rate Limit 控制 ──────────────────────────────────────────────
# 免費方案 40 rpm = 每 1.5 秒一個請求
RPM_LIMIT        = 40
RPM_MIN_INTERVAL = 60.0 / RPM_LIMIT          # 1.5 秒
RPM_MAX_BACKOFF  = 60.0                       # 單次最長等待
RPM_MAX_RETRIES  = 3                          # 同一模型 429 重試次數

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
    """針對特定模型的專屬參數。"""
    __slots__ = ("max_tokens", "thinking_budget", "temperature", "top_p")

    def __init__(
        self,
        max_tokens: int,
        thinking_budget: Optional[int] = None,
        temperature: float = 0.2,
        top_p: float = 0.95,
    ):
        self.max_tokens      = max_tokens
        self.thinking_budget = thinking_budget
        self.temperature     = temperature
        self.top_p           = top_p

# 依 NVIDIA 官方建議設定；有 thinking 的模型使用較高 temperature
MODEL_CONFIGS: Dict[str, ModelConfig] = {
    "nvidia/nemotron-3-ultra-550b-a55b": ModelConfig(
        max_tokens=16384,
        thinking_budget=16384,
        temperature=1.0,   # 官方 thinking 模型建議值
        top_p=0.95,
    ),
    "nvidia/llama-3.1-nemotron-ultra-253b-v1": ModelConfig(
        max_tokens=16384,
        thinking_budget=8192,
        temperature=1.0,
        top_p=0.95,
    ),
}
_DEFAULT_CONFIG = ModelConfig(max_tokens=4096, temperature=0.2)

# ─────────────────────────── 全域 OpenAI 客戶端（NVIDIA 端點） ──────
_nvidia_client: Optional[AsyncOpenAI] = None

def get_nvidia_client() -> AsyncOpenAI:
    """惰性建立並回傳共用的 AsyncOpenAI 客戶端。"""
    global _nvidia_client
    if _nvidia_client is None:
        _nvidia_client = AsyncOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=NVIDIA_API_KEY,
            timeout=300.0,
            max_retries=0,   # 我們自行控制 retry 邏輯
        )
    return _nvidia_client

# ─────────────────────────── Rate Limit Token Bucket ─────────────
class RateLimiter:
    """
    簡單的 async token bucket，確保請求間隔 ≥ RPM_MIN_INTERVAL。
    不做跨程序同步，適用單一 process 的 bot。
    """
    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._last_call_at: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now   = time.monotonic()
            wait  = self._min_interval - (now - self._last_call_at)
            if wait > 0:
                logger.debug("Rate limiter: 等待 %.2f 秒", wait)
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
    """
    以 streaming 方式呼叫單一模型，回傳完整文字（reasoning + content 合併）。
    遇到 429 時依 Retry-After header 或指數退避重試至 RPM_MAX_RETRIES 次。
    """
    cfg = MODEL_CONFIGS.get(model, _DEFAULT_CONFIG)

    extra_body = {}
    if cfg.thinking_budget is not None:
        extra_body = {
            "chat_template_kwargs": {"enable_thinking": True},
            "reasoning_budget": cfg.thinking_budget,
        }

    for attempt in range(1, RPM_MAX_RETRIES + 2):   # +2：最後一次失敗要 raise
        await _rate_limiter.acquire()
        try:
            full_text_parts: list[str] = []

            async with client.chat.completions.stream(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_content},
                ],
                temperature=cfg.temperature,
                top_p=cfg.top_p,
                max_tokens=cfg.max_tokens,
                extra_body=extra_body or None,
            ) as stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    # 收集 reasoning（思考過程，不回傳給使用者但計入 token）
                    reasoning = getattr(delta, "reasoning_content", None)
                    if reasoning:
                        pass   # 如需 debug 可在此 log
                    # 收集正式回應
                    if delta.content:
                        full_text_parts.append(delta.content)

            return "".join(full_text_parts)

        except RateLimitError as exc:
            if attempt > RPM_MAX_RETRIES:
                raise
            # 嘗試從 Retry-After header 取得等待秒數
            retry_after: float = RPM_MIN_INTERVAL * (2 ** attempt)   # 預設指數退避
            try:
                header_val = exc.response.headers.get("retry-after")
                if header_val:
                    retry_after = min(float(header_val), RPM_MAX_BACKOFF)
            except Exception:
                pass
            retry_after = min(retry_after, RPM_MAX_BACKOFF)
            logger.warning(
                "模型 %s 觸發 429（第 %d/%d 次），等待 %.1f 秒後重試",
                model, attempt, RPM_MAX_RETRIES, retry_after,
            )
            await asyncio.sleep(retry_after)


async def call_nvidia(system_prompt: str, user_content: str) -> Tuple[str, str]:
    """
    依序嘗試 NVIDIA_MODELS 列表中的模型（streaming）。
    回傳 (回應文字, 使用的模型名稱)；全部失敗則拋出 RuntimeError。
    """
    client    = get_nvidia_client()
    last_error = "（未發出任何請求）"

    for model in NVIDIA_MODELS:
        try:
            text = await _call_single_model(client, model, system_prompt, user_content)
            logger.info("NVIDIA API 成功使用模型：%s", model)
            return text, model

        except AuthenticationError as exc:
            # 認證失敗不必 fallback，直接中止
            raise RuntimeError(f"NVIDIA API 認證失敗，請確認 API Key：{exc}") from exc

        except RateLimitError as exc:
            # 已達最大重試仍 429 → 換下一模型
            last_error = f"429 Rate Limited: {exc}"
            logger.warning("模型 %s 達到重試上限，嘗試下一個模型", model)

        except APIStatusError as exc:
            last_error = f"HTTP {exc.status_code}: {exc.message[:200]}"
            logger.warning("模型 %s API 錯誤：%s", model, last_error)

        except Exception as exc:
            last_error = repr(exc)
            logger.warning("模型 %s 例外：%s", model, last_error)

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

_SEP_CODE_START   = "---OPTIMIZED_CODE---"
_SEP_CODE_END     = "---END_CODE---"
_SEP_REPORT_START = "---OPTIMIZATION_REPORT---"
_SEP_REPORT_END   = "---END_REPORT---"


def _parse_agent_response(raw: str) -> Tuple[str, str]:
    """從 AI 回應中解析出優化程式碼與報告；解析失敗則回傳原始文字。"""
    code = report = raw

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
    status_message_id: int,
    code: str,
    prompt_text: str,
    fname: str,
    bot,
) -> None:
    """背景工作：呼叫 AI 並將結果傳回 Telegram。"""
    try:
        opt_code, report, model = await run_agent(code, prompt_text)

        out_name = fname.removesuffix(".py") + "_optimized.py"
        buf = io.BytesIO(opt_code.encode("utf-8"))
        buf.name = out_name

        await bot.send_document(
            chat_id=chat_id,
            document=buf,
            filename=out_name,
            caption=f"✅ 完成！（模型：`{model}`）報告 👇",
            parse_mode="Markdown",
        )

        # 安靜地刪除 status 訊息；若已消失不報錯
        try:
            await bot.delete_message(chat_id=chat_id, message_id=status_message_id)
        except Exception:
            pass

        # 分段傳送報告（Telegram 單訊息上限 4096 字元）
        for i in range(0, len(report), TG_MSG_LIMIT):
            await bot.send_message(
                chat_id=chat_id,
                text=report[i : i + TG_MSG_LIMIT],
                parse_mode="Markdown",
            )

    except Exception:
        logger.exception("背景 run_agent 失敗（chat_id=%s）", chat_id)
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
    code        = ctx.user_data.get("code", "")
    fname       = ctx.user_data.get("filename", "code.py")
    prompt_text = update.message.text.strip()

    if not code:
        await update.message.reply_text("⚠️ 請先上傳 .py 檔案。")
        return WAIT_FILE

    msg = await update.message.reply_text(
        "⚙️ 收到需求！已排入背景運算，這可能需要 1~2 分鐘，請耐心等候…\n"
        "（您可以繼續進行其他操作）"
    )
    ctx.user_data.clear()

    asyncio.get_running_loop().create_task(
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
    app.add_handler(CommandHandler("help",  start))
    app.add_handler(conv)
    return app


def main() -> None:
    # Python 3.14 完全移除了 asyncio.get_event_loop() 在主執行緒的隱式建立。
    # python-telegram-bot 21.x 的 run_webhook() 內部仍呼叫該 API，
    # 因此必須在呼叫前手動建立並設定 event loop。
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = _build_app()

    webhook_url = f"{RENDER_URL}/webhook/{TELEGRAM_TOKEN}"
    masked_token = (
        f"{TELEGRAM_TOKEN[:5]}...[隱藏]...{TELEGRAM_TOKEN[-5:]}"
        if len(TELEGRAM_TOKEN) > 10 else "***"
    )
    logger.info("啟動 webhook server，port %d", PORT)
    logger.info("Webhook URL: %s/webhook/%s", RENDER_URL, masked_token)

    try:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url=webhook_url,
            url_path=f"/webhook/{TELEGRAM_TOKEN}",
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
    finally:
        # 確保 loop 正常關閉，釋放所有非同步資源
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            loop.close()


if __name__ == "__main__":
    main()
