import os
import re
import json
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
#MODEL_NAME     = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
#MODEL_NAME     = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
MAX_FILE_CHARS = int(os.getenv("MAX_FILE_CHARS", 80_000))
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

# ── Prompts ───────────────────────────────────────────────────────────────────

# 意圖分類：輕量判斷是否需要修改檔案，避免查詢類請求觸發不必要的寫入
INTENT_SYSTEM_PROMPT = """
判斷使用者的訊息是「查詢/閒聊」還是「修改網站」。
只回覆一個詞：QUERY 或 MODIFY。
- QUERY：使用者想查詢資訊、列出檔案、閒聊，或明確說不要修改任何東西
- MODIFY：使用者想新增、修改、刪除、重新設計網站任何內容
""".strip()

# 主要修改 Prompt，使用自訂分隔符避免 HTML 雙引號跳脫問題
SYSTEM_PROMPT = """
你是一位專業的前端開發工程師與網站設計師。你的任務是根據使用者的要求，修改公司網站的檔案。
公司網站目前位於 docs/ 資料夾，包含 HTML/CSS/JS 檔案。

【輸出格式規則】
請用以下格式輸出每一個需要新增或修改的檔案：

<<<FILENAME:index.html>>>
（完整的 index.html 內容，不可省略或截斷）
<<<END>>>

<<<FILENAME:style.css>>>
（完整的 style.css 內容）
<<<END>>>

<<<REPLY>>>
（必填）簡短說明做了哪些修改
<<<END>>>

【注意事項】
- FILENAME 後面只填檔名，不要加 docs/ 等目錄前綴
- 每個檔案區塊之間可以有空行
- <<<REPLY>>>...<<<END>>> 為必填，每次都必須輸出
- <<<END>>> 之後不要有任何多餘說明
- 保持設計現代、響應式、美觀
""".strip()


# ── 內部工具函式 ───────────────────────────────────────────────────────────────

def _classify_intent(user_message: str) -> str:
    """
    用輕量 LLM 呼叫判斷意圖，回傳 'QUERY' 或 'MODIFY'。
    若呼叫失敗，預設當作 MODIFY（安全側）。
    """
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user",   "content": user_message},
            ],
            temperature=0.0,
            max_tokens=5,
        )
        result = response.choices[0].message.content.strip().upper()
        logger.info("意圖分類結果：%s", result)
        return "QUERY" if "QUERY" in result else "MODIFY"
    except Exception as e:
        logger.warning("意圖分類失敗，預設為 MODIFY：%s", e)
        return "MODIFY"


def _answer_query(user_message: str, current_content: dict[str, str]) -> str:
    """針對查詢類請求，直接回答，不修改任何檔案。"""
    file_list = "\n".join(f"- {f}" for f in current_content.keys())
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": (
                    "你是網站助理，只回答使用者的問題，不修改任何檔案。"
                    "用繁體中文簡短回答。"
                )},
                {"role": "user", "content": (
                    f"使用者問題：{user_message}\n\n"
                    f"目前網站檔案清單：\n{file_list}"
                )},
            ],
            temperature=0.3,
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        # 即使 LLM 失敗，至少回傳檔案清單
        return f"目前網站檔案：\n{file_list}"


def _strip_docs_prefix(path: str) -> str:
    """移除 LLM 可能誤加的 docs/ 前綴，避免 github_utils 重複加後變成 docs/docs/。"""
    for prefix in ("docs/docs/", "docs/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _parse_llm_response(text: str) -> dict:
    """
    解析自訂分隔符格式，回傳：
    {"file_updates": [{"path": "...", "content": "..."}], "reply_message": "..."}
    """
    file_pattern = re.compile(
        r'<<<FILENAME:(?P<path>[^>]+)>>>\n(?P<content>.*?)<<<END>>>',
        re.DOTALL
    )
    reply_pattern = re.compile(
        r'<<<REPLY>>>\n(?P<reply>.*?)<<<END>>>',
        re.DOTALL
    )

    file_updates = [
        {"path": m.group("path").strip(), "content": m.group("content").rstrip("\n")}
        for m in file_pattern.finditer(text)
    ]
    reply_match = reply_pattern.search(text)
    reply = reply_match.group("reply").strip() if reply_match else "（AI 未提供修改說明）"

    if not file_updates:
        raise ValueError(f"LLM 回覆中找不到任何 <<<FILENAME:...>>> 區塊，原始回覆：{text[:300]}")

    return {"file_updates": file_updates, "reply_message": reply}


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
    """呼叫 LLM，回傳已解析的更新字典。失敗時最多重試 MAX_RETRIES 次。"""
    user_prompt = (
        f"使用者要求：{user_message}\n\n"
        f"目前網站檔案內容：\n{current_files_content}\n\n"
        "請依照系統指示的格式輸出，用 <<<FILENAME:檔名>>> ... <<<END>>> 包住每個檔案內容，"
        "並在最後輸出 <<<REPLY>>>...<<<END>>>。"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ]

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            logger.info("呼叫 LLM（第 %d 次）…", attempt)
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=TEMPERATURE,
            )
            raw = response.choices[0].message.content
            logger.info("LLM 原始回覆（前 500 字）：%s", raw[:500])
            return _parse_llm_response(raw)

        except ValueError as e:
            last_error = e
            logger.warning("解析失敗（第 %d 次）：%s", attempt, e)
        except Exception as e:
            last_error = Exception(f"AI 呼叫失敗：{e}")
            logger.error("LLM 呼叫異常（第 %d 次）：%s", attempt, e)
            break

    raise last_error


def apply_updates(updates: dict) -> tuple[bool, str]:
    """將 LLM 產生的檔案更新寫回 GitHub，回傳 (成功, 訊息)。"""
    file_updates = updates.get("file_updates", [])
    if not file_updates:
        return False, "LLM 未提供任何檔案更新內容。"

    for item in file_updates:
        raw_path = item.get("path", "").strip()
        content  = item.get("content", "")
        if not raw_path:
            logger.warning("跳過一筆缺少 path 的更新項目")
            continue

        path = _strip_docs_prefix(raw_path)
        if path != raw_path:
            logger.warning("已自動移除多餘路徑前綴：%s → %s", raw_path, path)

        logger.info("更新檔案：%s", path)
        success = update_or_create_file(path, content, f"AI 自動更新: {path}")
        if not success:
            return False, f"更新 {path} 失敗，請檢查 GitHub 權限或 API 限制。"

    reply = updates.get("reply_message") or "（AI 未提供修改說明）"
    return True, reply


def handle_user_message(user_message: str) -> str:
    """主要入口：意圖分類 → 查詢直接回答 / 修改則更新網站。"""
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

        # 2. 意圖分類
        intent = _classify_intent(user_message)

        # 3a. 查詢類：直接回答，不動檔案
        if intent == "QUERY":
            logger.info("意圖為查詢，跳過檔案修改")
            return _answer_query(user_message, current_content)

        # 3b. 修改類：LLM 產生更新並寫回 GitHub
        files_text = _build_files_text(current_content)
        updates = process_user_request(user_message, files_text)
        ok, result = apply_updates(updates)

        if ok:
            return f"✅ 網站已更新！\n{result}\n🔗 {SITE_URL}"
        return f"❌ 更新失敗：{result}"

    except Exception as e:
        logger.exception("處理訊息時發生嚴重錯誤")
        return f"❌ 系統發生錯誤：{e}"
