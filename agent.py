import os
import re
import json
import logging
from openai import OpenAI
from github_utils import (
    get_file_content,
    list_website_files,
    update_or_create_file,
    delete_file,
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
MAX_RETRIES    = int(os.getenv("AGENT_MAX_RETRIES", 2))
TEMPERATURE    = float(os.getenv("AGENT_TEMPERATURE", 0.3))
PREVIEW_CHARS  = int(os.getenv("PREVIEW_CHARS", 800))   # 規劃階段每檔摘要長度

SITE_URL = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/"

# 禁止刪除的核心檔案
PROTECTED_FILES = frozenset({"index.html", "style.css", ".nojekyll"})

# ── OpenAI client ─────────────────────────────────────────────────────────────
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
    timeout=600.0,
    max_retries=3,
)

# ── 意圖分類（關鍵字，零延遲） ─────────────────────────────────────────────────
QUERY_KEYWORDS = (
    "只回報", "只列出", "列出", "查詢", "回報", "有哪些", "哪些檔案",
    "什麼都不要改", "不要修改", "不需要改", "不用改", "告訴我",
)

def _classify_intent(msg: str) -> str:
    for kw in QUERY_KEYWORDS:
        if kw in msg:
            logger.info("意圖：QUERY（%s）", kw)
            return "QUERY"
    logger.info("意圖：MODIFY")
    return "MODIFY"

def _answer_query(current_content: dict) -> str:
    file_list = "\n".join(f"• {f}" for f in sorted(current_content.keys()))
    return f"📁 目前網站共有 {len(current_content)} 個檔案：\n{file_list}"

def _strip_docs_prefix(path: str) -> str:
    for prefix in ("docs/docs/", "docs/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path

def _llm(messages: list, max_tokens: int = 4096) -> str:
    """統一的 LLM 呼叫，含重試。"""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=max_tokens,
            )
            return resp.choices[0].message.content
        except Exception as e:
            last_error = e
            logger.warning("LLM 呼叫失敗（第 %d 次）：%s", attempt, e)
    raise Exception(f"LLM 呼叫失敗：{last_error}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 1：規劃 — LLM 決定要動哪些檔案、做什麼
# ══════════════════════════════════════════════════════════════════════════════

PLAN_SYSTEM = """
你是資深前端架構師。根據使用者需求與現有網站結構，規劃需要的檔案異動。

只輸出一個合法 JSON，格式如下：
{
  "summary": "簡短說明這次改動的目標",
  "files_to_update": ["index.html", "style.css"],
  "files_to_create": ["contact.html"],
  "files_to_delete": ["old.html"],
  "context_files": ["style.css"]
}

欄位說明：
- files_to_update：需要修改的現有檔案
- files_to_create：需要新建的檔案
- files_to_delete：需要刪除的檔案（謹慎使用）
- context_files：生成時需要參考的檔案（例如生成新 HTML 時需要參考 style.css 的 class 名稱）

只輸出 JSON，不要有任何其他文字。
""".strip()

def _step1_plan(user_message: str, current_content: dict) -> dict:
    """Step 1：規劃階段，每個檔案只傳摘要，快速讓 LLM 理解網站結構。"""
    # 建立檔案摘要（只傳前 PREVIEW_CHARS 字元）
    file_summary = []
    for fname, content in current_content.items():
        preview = content[:PREVIEW_CHARS]
        truncated = "...（已截斷）" if len(content) > PREVIEW_CHARS else ""
        file_summary.append(f"=== {fname} ({len(content)} 字元) ===\n{preview}{truncated}")
    files_overview = "\n\n".join(file_summary)

    prompt = (
        f"使用者需求：{user_message}\n\n"
        f"現有檔案概覽：\n{files_overview}\n\n"
        "請輸出規劃 JSON。"
    )
    raw = _llm([
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user",   "content": prompt},
    ], max_tokens=512)

    logger.info("Step1 規劃結果：%s", raw[:300])

    # 解析 JSON
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"規劃階段無法解析 JSON：{raw[:200]}")
    plan = json.loads(match.group(0))

    # 過濾受保護檔案
    to_delete = [f for f in plan.get("files_to_delete", []) if f not in PROTECTED_FILES]
    skipped   = [f for f in plan.get("files_to_delete", []) if f in PROTECTED_FILES]
    if skipped:
        logger.warning("規劃階段：以下受保護檔案已從刪除清單移除：%s", skipped)
    plan["files_to_delete"] = to_delete
    plan["_skipped_protected"] = skipped

    return plan


# ══════════════════════════════════════════════════════════════════════════════
# Step 2：逐檔生成 — 每個檔案獨立一次 LLM 呼叫
# ══════════════════════════════════════════════════════════════════════════════

GENERATE_SYSTEM = """
你是專業前端工程師。你的任務是生成或修改「單一檔案」的完整內容。

規則：
1. 只輸出該檔案的完整內容，不要有任何說明文字、標記或 markdown。
2. 內容必須完整，不可省略或截斷。
3. HTML 標籤必須正確閉合，JS 括號必須配對。
4. 保持設計現代、響應式、美觀。
""".strip()

def _step2_generate_file(
    filename: str,
    action: str,          # "update" 或 "create"
    user_message: str,
    plan_summary: str,
    existing_content: str | None,
    context_files: dict[str, str],
) -> str:
    """Step 2：為單一檔案呼叫 LLM 生成完整內容。"""

    context_text = ""
    if context_files:
        parts = [f"=== {k} ===\n{v}" for k, v in context_files.items()]
        context_text = "參考檔案：\n" + "\n\n".join(parts) + "\n\n"

    if action == "update" and existing_content:
        task = (
            f"請修改 {filename}，根據以下需求：{user_message}\n"
            f"整體規劃說明：{plan_summary}\n\n"
            f"{context_text}"
            f"現有 {filename} 內容：\n{existing_content}\n\n"
            f"請輸出修改後的完整 {filename} 內容："
        )
    else:
        task = (
            f"請建立全新的 {filename}，根據以下需求：{user_message}\n"
            f"整體規劃說明：{plan_summary}\n\n"
            f"{context_text}"
            f"請輸出完整的 {filename} 內容："
        )

    logger.info("Step2 生成檔案：%s（%s）", filename, action)
    content = _llm([
        {"role": "system", "content": GENERATE_SYSTEM},
        {"role": "user",   "content": task},
    ], max_tokens=4096)

    # 清除可能的 markdown 包裝
    content = re.sub(r'^```[\w]*\n?', '', content.strip())
    content = re.sub(r'\n?```$', '', content.strip())
    return content.strip()


# ══════════════════════════════════════════════════════════════════════════════
# Step 3：基本驗證
# ══════════════════════════════════════════════════════════════════════════════

def _step3_validate(filename: str, content: str) -> list[str]:
    """Step 3：基本語法驗證，回傳警告清單（不阻擋寫入，只記錄）。"""
    warnings = []
    if filename.endswith(".html"):
        if "<!DOCTYPE" not in content and "<html" not in content:
            warnings.append("缺少 <!DOCTYPE> 或 <html> 標籤")
        open_tags  = len(re.findall(r'<body[^>]*>', content))
        close_tags = len(re.findall(r'</body>', content))
        if open_tags != close_tags:
            warnings.append(f"<body> 標籤不配對（開 {open_tags} / 閉 {close_tags}）")
    if filename.endswith(".js"):
        opens  = content.count('{')
        closes = content.count('}')
        if abs(opens - closes) > 2:
            warnings.append(f"JS 大括號不配對（{{ {opens} / }} {closes}）")
    return warnings


# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def handle_user_message(user_message: str) -> str:
    try:
        # 讀取現有檔案
        files = list_website_files()
        current_content: dict[str, str] = {}
        for f in files:
            data = get_file_content(f)
            if data is not None:
                current_content[f] = data

        if not current_content:
            return "❌ 無法讀取任何網站檔案，請確認 GitHub 存取設定。"

        # 查詢類直接回答
        if _classify_intent(user_message) == "QUERY":
            return _answer_query(current_content)

        # ── Step 1：規劃 ──────────────────────────────────────────────────────
        logger.info("=== Step 1：規劃階段 ===")
        plan = _step1_plan(user_message, current_content)
        logger.info("規劃：更新%s 新建%s 刪除%s 參考%s",
                    plan.get("files_to_update", []),
                    plan.get("files_to_create", []),
                    plan.get("files_to_delete", []),
                    plan.get("context_files", []))

        # ── Step 2：逐檔生成 ──────────────────────────────────────────────────
        logger.info("=== Step 2：逐檔生成階段 ===")
        plan_summary = plan.get("summary", "")
        context_files = {
            f: current_content[f]
            for f in plan.get("context_files", [])
            if f in current_content
        }
        generated: dict[str, str] = {}

        for fname in plan.get("files_to_update", []):
            generated[fname] = _step2_generate_file(
                fname, "update", user_message, plan_summary,
                current_content.get(fname), context_files,
            )
        for fname in plan.get("files_to_create", []):
            generated[fname] = _step2_generate_file(
                fname, "create", user_message, plan_summary,
                None, context_files,
            )

        # ── Step 3：驗證 ──────────────────────────────────────────────────────
        logger.info("=== Step 3：驗證階段 ===")
        for fname, content in generated.items():
            warns = _step3_validate(fname, content)
            if warns:
                logger.warning("檔案 %s 驗證警告：%s", fname, warns)

        # ── Step 4：寫入 GitHub ───────────────────────────────────────────────
        logger.info("=== Step 4：寫入階段 ===")
        for fname, content in generated.items():
            logger.info("寫入：%s", fname)
            ok = update_or_create_file(fname, content, f"AI 自動更新: {fname}")
            if not ok:
                return f"❌ 寫入 {fname} 失敗，請檢查 GitHub 權限。"

        for fname in plan.get("files_to_delete", []):
            logger.info("刪除：%s", fname)
            delete_file(fname, f"AI 自動刪除: {fname}")

        # ── 回覆訊息 ──────────────────────────────────────────────────────────
        updated = plan.get("files_to_update", []) + plan.get("files_to_create", [])
        deleted = plan.get("files_to_delete", [])
        skipped = plan.get("_skipped_protected", [])

        reply_parts = [f"✅ 網站已更新！\n📋 {plan_summary}"]
        if updated:
            reply_parts.append("📝 已更新／新增：" + ", ".join(updated))
        if deleted:
            reply_parts.append("🗑️ 已刪除：" + ", ".join(deleted))
        if skipped:
            reply_parts.append("⚠️ 受保護未刪除：" + ", ".join(skipped))
        reply_parts.append(f"🔗 {SITE_URL}")

        return "\n".join(reply_parts)

    except Exception as e:
        logger.exception("處理訊息時發生嚴重錯誤")
        return f"❌ 系統發生錯誤：{e}"
