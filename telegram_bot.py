"""
Telegram Bot — Python 程式碼優化機器人
流程：上傳 .py + prompt → Agent 優化 → 回傳優化後 .py + 報告
"""

import os
import io
import logging
import asyncio
from pathlib import Path
from telegram import Update, Document
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)
from telegram.constants import ParseMode

from agent_harness import optimize_code, format_changes_report

# ── 設定 ──────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MAX_FILE_SIZE = 100 * 1024  # 100 KB

# ConversationHandler 狀態
WAIT_PROMPT = 1

# 暫存用戶上傳的程式碼 {user_id: {"filename": str, "code": str}}
user_sessions: dict[int, dict] = {}


# ── 指令處理器 ────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Python 程式碼優化機器人*\n\n"
        "使用方式：\n"
        "1️⃣ 直接上傳 `.py` 檔案\n"
        "2️⃣ 機器人會請你輸入優化需求\n"
        "3️⃣ 等待 NVIDIA AI 分析優化\n"
        "4️⃣ 收到優化後的 `.py` 與詳細報告\n\n"
        "⚡ 由 *NVIDIA NIM API* 驅動\n"
        "輸入 /help 查看更多說明",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *指令說明*\n\n"
        "/start — 開始使用\n"
        "/help — 顯示此說明\n"
        "/cancel — 取消目前操作\n\n"
        "📝 *優化需求範例*：\n"
        "• `優化效能，減少記憶體使用`\n"
        "• `改善可讀性，加上型別標注`\n"
        "• `加強錯誤處理與日誌記錄`\n"
        "• `重構為物件導向設計`\n"
        "• `全面優化，包含效能與安全性`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    user_sessions.pop(uid, None)
    await update.message.reply_text("❌ 已取消操作。")
    return ConversationHandler.END


# ── 檔案接收 ──────────────────────────────────────────

async def receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收 .py 檔案，進入等待 prompt 狀態"""
    message = update.message
    document: Document = message.document
    uid = update.effective_user.id

    # 驗證副檔名
    if not document.file_name.endswith(".py"):
        await message.reply_text("⚠️ 請上傳 `.py` 格式的 Python 檔案。")
        return ConversationHandler.END

    # 驗證大小
    if document.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            f"⚠️ 檔案過大（{document.file_size // 1024} KB）。\n"
            f"最大支援 {MAX_FILE_SIZE // 1024} KB。"
        )
        return ConversationHandler.END

    # 下載檔案內容
    processing_msg = await message.reply_text("📥 正在接收檔案...")
    try:
        tg_file = await document.get_file()
        file_bytes = await tg_file.download_as_bytearray()
        code = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        await processing_msg.edit_text("❌ 無法讀取檔案（請確認為 UTF-8 編碼）。")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"File download error: {e}")
        await processing_msg.edit_text("❌ 下載檔案時發生錯誤，請稍後再試。")
        return ConversationHandler.END

    # 儲存到 session
    user_sessions[uid] = {
        "filename": document.file_name,
        "code": code,
        "lines": len(code.splitlines()),
    }

    await processing_msg.edit_text(
        f"✅ 已接收 `{document.file_name}`\n"
        f"📏 {len(code.splitlines())} 行程式碼\n\n"
        f"💬 請輸入你的*優化需求*（或直接傳送 `全面優化`）：",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAIT_PROMPT


# ── Prompt 處理與優化執行 ──────────────────────────────

async def receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """接收優化 prompt，呼叫 Agent Harness 執行優化"""
    uid = update.effective_user.id
    user_prompt = update.message.text.strip()

    session = user_sessions.get(uid)
    if not session:
        await update.message.reply_text("⚠️ 找不到上傳的檔案，請重新上傳 `.py` 檔案。")
        return ConversationHandler.END

    filename = session["filename"]
    code = session["code"]

    # 顯示進度訊息
    progress_msg = await update.message.reply_text(
        f"⚙️ *Agent 優化中...*\n\n"
        f"📄 檔案：`{filename}`\n"
        f"🎯 需求：{user_prompt[:100]}\n\n"
        f"🤖 NVIDIA AI 正在分析程式碼，請稍候...\n"
        f"（通常需要 15-60 秒）",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        # 呼叫 Agent Harness
        result = await optimize_code(code=code, user_prompt=user_prompt)

        # 格式化優化報告
        report = format_changes_report(result)
        
        # 生成優化後的檔名
        base = Path(filename).stem
        optimized_filename = f"{base}_optimized.py"

        # 回傳優化報告
        await progress_msg.edit_text(report, parse_mode=ParseMode.MARKDOWN)

        # 回傳優化後的 .py 檔案
        file_buffer = io.BytesIO(result.optimized_code.encode("utf-8"))
        file_buffer.name = optimized_filename
        
        await update.message.reply_document(
            document=file_buffer,
            filename=optimized_filename,
            caption=(
                f"📦 `{optimized_filename}`\n"
                f"原始 {session['lines']} 行 → 優化後 {result.metrics.get('optimized_lines', '?')} 行"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        # 如果有多項重要改動，額外發送純文字優化說明
        high_impact = [c for c in result.changes if c.get("impact") == "high"]
        if high_impact:
            detail_lines = ["🔴 *重要優化項目詳述*：\n"]
            for i, c in enumerate(high_impact, 1):
                detail_lines.append(f"*{i}. {c.get('category')}*\n{c.get('description')}\n")
            await update.message.reply_text(
                "\n".join(detail_lines),
                parse_mode=ParseMode.MARKDOWN,
            )

    except httpx.HTTPStatusError as e:
        logger.error(f"NVIDIA API error: {e.response.status_code} - {e.response.text}")
        await progress_msg.edit_text(
            f"❌ *NVIDIA API 錯誤*\n"
            f"狀態碼：`{e.response.status_code}`\n"
            f"請確認 API Key 是否有效或帳戶額度是否充足。",
            parse_mode=ParseMode.MARKDOWN,
        )
    except asyncio.TimeoutError:
        await progress_msg.edit_text(
            "⏱️ *請求逾時*\n程式碼可能太大，請嘗試縮小範圍後重新上傳。",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Optimization error: {e}", exc_info=True)
        await progress_msg.edit_text(
            f"❌ *優化失敗*\n`{str(e)[:200]}`\n\n請稍後重試。",
            parse_mode=ParseMode.MARKDOWN,
        )
    finally:
        user_sessions.pop(uid, None)

    return ConversationHandler.END


# ── 非預期訊息處理 ────────────────────────────────────

async def unexpected_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📂 請上傳一個 `.py` 檔案開始優化流程。\n"
        "輸入 /help 查看使用說明。"
    )


# ── 主程式 ────────────────────────────────────────────

def main() -> None:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # 對話流程：上傳檔案 → 輸入 prompt → 優化完成
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Document.ALL, receive_file)
        ],
        states={
            WAIT_PROMPT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_prompt,
                )
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=300,  # 5 分鐘逾時
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(conv_handler)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, unexpected_message)
    )

    logger.info("🚀 Bot 啟動中...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
