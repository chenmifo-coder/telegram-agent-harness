import os
import json
import re
import logging
from openai import OpenAI
from github_utils import (
    get_file_content,
    list_website_files,
    update_or_create_file,
    REPO_OWNER,
    REPO_NAME,
)

# ── 設定 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

NVIDIA_API_KEY = os.environ["NVIDIA_API_KEY"]
MODEL_NAME     = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", 80_000))   # 避免超出 context window
MAX_RETRIES    = int(os.getenv("AGENT_MAX_RETRIES", 2))
TEMPERATURE    = float(os.getenv("AGENT_TEMPERATURE", 0.3))

SITE_URL = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/"

# ── OpenAI client ─────────────────────────────────────────────────────────────
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
    timeout=600.0,
    max_retries=3,
)

# ── Prompt ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """
你是一位專業的前端開發工程師與網站設計師。你的任務是根據使用者的要求，修改公司網站的檔案。
公司網站目前位於 `docs/` 資料夾，包含 HTML/CSS/JS 檔案。
你必須輸出一個嚴格符合以下格式的 JSON，**絕對不可包含任何其他文字或標記**：

{
  "file_updates": [
    {
      "path": "index.html",
      "content": "完整的 HTML 內容"
    },
    {
      "path": "style.css",
      "content": "完整的 CSS 內容"
    }
  ],
  "reply_message": "簡短回覆使用者做了哪些修改"
}

若使用者要求全新的設計風格或新增頁面，請直接產生對應的新檔案內容。
保持設計現代、響應式、美觀。
""".strip()


# ── 內部工具函式 ───────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """從 LLM 回覆中可靠地提取第一個完整 JSON 物件。"""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        raise ValueError("LLM 回覆中找不到有效的 JSON 結構")
    return json.loads(match.group(0))


def _build_files_text(current_content: dict[str, str]) -> str:
    """將檔案字典組合成提示用文字，並在超出限制時截斷。"""
    parts = []
    total = 0
    for path, content in current_content.items():
        snippet = content[:MAX_FILE_CHARS - total] if total < MAX_FILE_CHARS else ""
        parts.append(f"=== {path} ===\n{snippet}")
        total += len(snippet)
        if total >= MAX_FILE_CHARS:
            logger.warning("檔案內容已截斷，已達 %d 字元上限", MAX_FILE_CHARS)
            parts.append("...(內容過長，已截斷)")
            break
    return "\n\n".join(parts)


# ── 核心邏輯 ──────────────────────────────────────────────────────────────────

def process_user_request(user_message: str, current_files_content: str) -> dict:
    """
    呼叫 LLM，回傳已解析的更新字典。
    失敗時最多重試 MAX_RETRIES 次。
    """
    user_prompt = (
        f"使用者要求：{user_message}\n\n"
        f"目前網站檔案內容：\n{current_files_content}\n"
        "請輸出 JSON 更新。"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ]

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):   # 1 次正常 + MAX_RETRIES 次重試
        try:
            logger.info("呼叫 LLM（第 %d 次）…", attempt)
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=TEMPERATURE,
            )
            raw = response.choices[0].message.content
            logger.debug("LLM 原始回覆：%s", raw[:300])
            return _extract_json(raw)

        except json.JSONDecodeError as e:
            last_error = ValueError(
                "AI 產生的內容包含無效 JSON（可能有未跳脫的引號或回覆被截斷）。"
                f"詳細：{e}"
            )
            logger.warning("JSON 解析失敗（第 %d 次）：%s", attempt, e)
        except ValueError as e:
            last_error = e
            logger.warning("無法提取 JSON（第 %d 次）：%s", attempt, e)
        except Exception as e:
            last_error = Exception(f"AI 呼叫失敗：{e}")
            logger.error("LLM 呼叫異常（第 %d 次）：%s", attempt, e)
            break   # 非解析類錯誤不重試

    raise last_error


def apply_updates(updates: dict) -> tuple[bool, str]:
    """將 LLM 產生的檔案更新寫回 GitHub，回傳 (成功, 訊息)。"""
    file_updates = updates.get("file_updates", [])
    if not file_updates:
        return False, "LLM 未提供任何檔案更新內容。"

    for item in file_updates:
        path    = item.get("path", "").strip()
        content = item.get("content", "")
        if not path:
            logger.warning("跳過一筆缺少 path 的更新項目")
            continue

        logger.info("更新檔案：%s", path)
        success = update_or_create_file(path, content, f"AI 自動更新: {path}")
        if not success:
            return False, f"更新 {path} 失敗，請檢查 GitHub 權限或 API 限制。"

    reply = updates.get("reply_message") or "（AI 未提供修改說明）"
    return True, reply


def handle_user_message(user_message: str) -> str:
    """主要入口：讀取現有網站 → 規劃更新 → 套用更新 → 回傳結果訊息。"""
    try:
        # 1. 讀取現有網站所有檔案
        files = list_website_files()
        current_content: dict[str, str] = {}
        for f in files:
            data = get_file_content(f)
            if data is not None:
                current_content[f] = data

        if not current_content:
            return "❌ 無法讀取任何網站檔案，請確認 GitHub 存取設定。"

        files_text = _build_files_text(current_content)

        # 2. LLM 規劃更新
        updates = process_user_request(user_message, files_text)

        # 3. 套用更新
        ok, result = apply_updates(updates)
        if ok:
            return f"✅ 網站已更新！\n{result}\n🔗 {SITE_URL}"
        return f"❌ 更新失敗：{result}"

    except Exception as e:
        logger.exception("處理訊息時發生嚴重錯誤")
        return f"❌ 系統發生錯誤：{e}"
