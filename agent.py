import os
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
#MODEL_NAME     = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
#MODEL_NAME     = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
MAX_FILE_CHARS      = int(os.getenv("MAX_FILE_CHARS", 80_000))
# 每個檔案在摘要模式下最多顯示的字元數（約前 60 行）
PREVIEW_CHARS_PER_FILE = int(os.getenv("PREVIEW_CHARS_PER_FILE", 1_500))
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

# 【修正】意圖分類改用關鍵字比對，完全不呼叫 LLM，避免耗時與誤判
QUERY_KEYWORDS = (
    "只回報", "只列出", "列出", "查詢", "回報", "有哪些", "哪些檔案",
    "什麼都不要改", "不要修改", "不需要改", "不用改", "告訴我",
)

def _classify_intent(user_message: str) -> str:
    """
    用關鍵字快速判斷意圖，回傳 'QUERY' 或 'MODIFY'。
    不呼叫 LLM，零延遲，不會誤判。
    """
    for kw in QUERY_KEYWORDS:
        if kw in user_message:
            logger.info("意圖分類（關鍵字比對）：QUERY（命中關鍵字：%s）", kw)
            return "QUERY"
    logger.info("意圖分類（關鍵字比對）：MODIFY")
    return "MODIFY"


def _answer_query(current_content: dict[str, str]) -> str:
    """查詢類：直接回傳檔案清單，完全不呼叫 LLM。"""
    file_list = "\n".join(f"• {f}" for f in sorted(current_content.keys()))
    return f"📁 目前網站共有 {len(current_content)} 個檔案：\n{file_list}"


def _strip_docs_prefix(path: str) -> str:
    """移除 LLM 可能誤加的 docs/ 前綴，避免變成 docs/docs/。"""
    for prefix in ("docs/docs/", "docs/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _parse_llm_response(text: str) -> dict:
    """
    解析自訂分隔符格式，回傳：
    {"file_updates": [{"path": "...", "content": "..."}], "reply_message": "..."}
    """
    # >>{2,3} 容錯：允許模型輸出 >> 或 >>> 結尾（常見筆誤）
    # \s* 容錯：允許標記與內容之間有多餘空白或換行
    file_pattern = re.compile(
        r'<<<FILENAME:(?P<path>[^>\n]+?)>{2,3}\s*\n(?P<content>.*?)<<<END>{2,3}',
        re.DOTALL
    )
    reply_pattern = re.compile(
        r'<<<REPLY>{2,3}\s*\n?(?P<reply>.*?)<<<END>{2,3}',
        re.DOTALL
    )

    file_updates = [
        {"path": m.group("path").strip(), "content": m.group("content").rstrip("\n")}
        for m in file_pattern.finditer(text)
    ]

    # 優先用 <<<REPLY>>> 區塊；若沒有，抓回覆開頭第一段非空白文字當 fallback
    reply_match = reply_pattern.search(text)
    if reply_match:
        reply = reply_match.group("reply").strip()
    else:
        # fallback：取第一個 <<< 標記之前的文字（模型可能把說明放最前面但忘了包標記）
        pre_marker = text.split("<<<")[0].strip()
        reply = pre_marker if pre_marker else "（AI 未提供修改說明）"
        if reply != "（AI 未提供修改說明）":
            logger.warning("<<<REPLY>>> 區塊缺失，使用前綴文字作為說明：%s", reply[:80])

    if not file_updates:
        raise ValueError(f"LLM 回覆中找不到任何 <<<FILENAME:...>>> 區塊，原始回覆：{text[:300]}")

    return {"file_updates": file_updates, "reply_message": reply}


def _build_files_text(current_content: dict[str, str], preview_only: bool = False) -> str:
    """
    將檔案字典組合成提示用文字。
    preview_only=True：每個檔案只傳前 PREVIEW_CHARS_PER_FILE 字元的預覽（適合小修改）。
    preview_only=False：傳完整內容，總量上限 MAX_FILE_CHARS（適合大改動）。
    """
    parts = []
    total = 0

    for path, content in current_content.items():
        if preview_only:
            snippet = content[:PREVIEW_CHARS_PER_FILE]
            truncated = len(content) > PREVIEW_CHARS_PER_FILE
            block = f"=== {path} ({len(content)} 字元) ===\n{snippet}"
            if truncated:
                block += f"\n...（僅顯示前 {PREVIEW_CHARS_PER_FILE} 字元，完整內容已省略）"
        else:
            remaining = MAX_FILE_CHARS - total
            if remaining <= 0:
                break
            snippet = content[:remaining]
            truncated = len(content) > remaining
            block = f"=== {path} ===\n{snippet}"
            if truncated:
                logger.warning("檔案 %s 內容已截斷（總量達上限 %d）", path, MAX_FILE_CHARS)
                block += "\n...（內容過長，已截斷）"
            total += len(snippet)

        parts.append(block)

    return "\n\n".join(parts)


def _estimate_scope(user_message: str) -> str:
    """
    根據使用者訊息長度與關鍵字，判斷是小修改（preview）還是大改動（full）。
    回傳 'preview' 或 'full'。
    """
    FULL_KEYWORDS = (
        "重新設計", "打掉重練", "全新", "整個網站", "所有頁面",
        "完全改", "重構", "重寫", "新增頁面", "新增一個",
    )
    for kw in FULL_KEYWORDS:
        if kw in user_message:
            logger.info("改動範圍判斷：full（命中關鍵字：%s）", kw)
            return "full"
    logger.info("改動範圍判斷：preview（小修改，使用摘要模式）")
    return "preview"


# ── 核心邏輯 ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
你是一位專業的前端開發工程師與網站設計師。你的任務是根據使用者的要求，修改公司網站的檔案。
公司網站目前位於 docs/ 資料夾，包含 HTML/CSS/JS 檔案。

【輸出格式規則】
請嚴格按照以下順序輸出，先輸出說明，再輸出檔案：

第一步，先輸出修改說明：
<<<REPLY>>>
（必填）簡短說明做了哪些修改，例如：新增 contact.html 聯絡頁面，並更新 index.html 導覽列
<<<END>>>

第二步，再輸出每一個需要新增或修改的檔案：
<<<FILENAME:contact.html>>>
（完整的 contact.html 內容，不可省略或截斷）
<<<END>>>

<<<FILENAME:index.html>>>
（完整的 index.html 內容）
<<<END>>>

【注意事項】
- <<<REPLY>>> 必須是第一個區塊，在所有 FILENAME 之前
- FILENAME 後面只填檔名，不要加 docs/ 等目錄前綴
- 每個檔案區塊之間可以有空行
- <<<END>>> 之後不要有任何多餘說明
- 保持設計現代、響應式、美觀
""".strip()


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

        # 2. 關鍵字意圖分類（零延遲，不呼叫 LLM）
        intent = _classify_intent(user_message)

        # 3a. 查詢類：直接回傳檔案清單，不動任何檔案
        if intent == "QUERY":
            return _answer_query(current_content)

        # 3b. 修改類：LLM 產生更新並寫回 GitHub
        scope = _estimate_scope(user_message)
        files_text = _build_files_text(current_content, preview_only=(scope == "preview"))
        updates = process_user_request(user_message, files_text)
        ok, result = apply_updates(updates)

        if ok:
            return f"✅ 網站已更新！\n{result}\n🔗 {SITE_URL}"
        return f"❌ 更新失敗：{result}"

    except Exception as e:
        logger.exception("處理訊息時發生嚴重錯誤")
        return f"❌ 系統發生錯誤：{e}"
