import os
import re
import json
import logging
import datetime
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
PREVIEW_CHARS  = int(os.getenv("PREVIEW_CHARS", 600))

SITE_URL        = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/"
PROTECTED_FILES = frozenset({"index.html", "style.css", ".nojekyll"})

# Harness 設定檔路徑（相對於 docs/ 往上一層）
HARNESS_PATHS = {
    "design_system": "../harness/design_system.json",
    "components":    "../harness/components.json",
    "site_map":      "../harness/site_map.json",
    "memory":        "../harness/memory.json",
}

# ── OpenAI client ─────────────────────────────────────────────────────────────
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
    timeout=600.0,
    max_retries=3,
)

# ── 意圖分類 ──────────────────────────────────────────────────────────────────
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

def _strip_docs_prefix(path: str) -> str:
    for prefix in ("docs/docs/", "docs/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path

def _llm(messages: list, max_tokens: int = 4096) -> str:
    """統一 LLM 呼叫，含重試。"""
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
# Harness 讀取 / 寫入
# ══════════════════════════════════════════════════════════════════════════════

def _load_harness() -> dict:
    """從 GitHub 讀取所有 Harness 設定檔。"""
    harness = {}
    for key, path in HARNESS_PATHS.items():
        raw = get_file_content(path)
        if raw:
            try:
                harness[key] = json.loads(raw)
                logger.info("Harness 讀取成功：%s", key)
            except json.JSONDecodeError:
                logger.warning("Harness JSON 解析失敗：%s", key)
                harness[key] = {}
        else:
            logger.warning("Harness 設定不存在：%s（請先執行 harness_init.py）", key)
            harness[key] = {}
    return harness

def _save_harness(key: str, data: dict):
    """將 Harness 設定寫回 GitHub。"""
    path = HARNESS_PATHS[key]
    content = json.dumps(data, ensure_ascii=False, indent=2)
    ok = update_or_create_file(path, content, f"Harness 自動更新: {key}")
    if not ok:
        logger.warning("Harness 寫入失敗：%s", key)

# ── 設計規範 Context 組裝 ─────────────────────────────────────────────────────

def _build_design_context(harness: dict) -> str:
    """
    組合設計系統 + 元件模板 → 注入每次 LLM 生成的 prompt。
    這是 Harness 的核心：給 LLM 明確的設計標準，不讓它自由發揮。
    """
    ds = harness.get("design_system", {})
    co = harness.get("components", {})

    # 色彩
    colors = ds.get("colors", {})
    color_lines = "\n".join(f"  --{k.replace('_','-')}: {v};" for k, v in colors.items())

    # 設計規則
    rules = "\n".join(f"  {i+1}. {r}" for i, r in enumerate(ds.get("style_rules", [])))

    # 元件模板
    def _comp(key: str, field: str) -> str:
        return co.get(key, {}).get(field, "（未定義）")

    return f"""
【設計系統 CSS 變數（必須遵守）】
:root {{
{color_lines}
}}

【設計規則（必須全部遵守）】
{rules}

【字體載入（必須放在 <head>）】
{ds.get('fonts', {}).get('cdn', '')}

【固定元件：全域 CSS（複製到 style.css 或 <style>）】
{_comp('global_css', 'css')}

【固定元件：按鈕 CSS】
{_comp('button', 'css')}

【固定元件：卡片 CSS】
{_comp('card', 'css')}

【固定元件：導覽列 HTML（{{{{NAV_ITEMS}}}} 替換為實際 <li> 清單）】
{_comp('nav', 'html')}

【固定元件：導覽列 CSS】
{_comp('nav', 'css')}

【固定元件：Hero 區塊 HTML（替換 {{{{...}}}} 佔位符）】
{_comp('hero', 'html')}

【固定元件：Hero 區塊 CSS】
{_comp('hero', 'css')}

【固定元件：頁腳 HTML】
{_comp('footer', 'html')}

【固定元件：頁腳 CSS】
{_comp('footer', 'css')}

【頁面骨架（每個 HTML 必須基於此結構）】
{_comp('page_shell', 'html')}
""".strip()

def _build_nav_items(site_map: dict, current_page: str = "") -> str:
    """根據 site_map 生成導覽列 <li> 項目。"""
    links = site_map.get("nav_links", [])
    items = []
    for link in links:
        active = " active" if link["href"] == current_page else ""
        items.append(
            f'<li><a href="{link["href"]}" class="nav-link{active}">{link["label"]}</a></li>'
        )
    return "\n      ".join(items)

def _answer_query(current_content: dict, site_map: dict) -> str:
    pages = site_map.get("pages", {})
    lines = []
    for fname in sorted(current_content.keys()):
        desc = pages.get(fname, {}).get("description", "")
        lines.append(f"• {fname}" + (f" — {desc}" if desc else ""))
    return f"📁 目前網站共有 {len(current_content)} 個檔案：\n" + "\n".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# Step 1：規劃
# ══════════════════════════════════════════════════════════════════════════════

PLAN_SYSTEM = """
你是資深前端架構師。根據使用者需求與現有網站結構，規劃需要的檔案異動。
只輸出一個合法 JSON，不要有任何其他文字：

{
  "summary": "簡短說明這次改動目標（繁體中文）",
  "files_to_update": ["index.html"],
  "files_to_create": ["contact.html"],
  "files_to_delete": [],
  "context_files": ["style.css"],
  "nav_needs_update": true,
  "memory_note": "值得記住的設計決定（可空字串）"
}

規則：
- files_to_delete 只在使用者明確要求時填入
- nav_needs_update 為 true 時，所有含導覽列的頁面都需要更新
- context_files 填入生成時需要參考的現有檔案（最多 2 個）
""".strip()

def _step1_plan(user_message: str, current_content: dict, harness: dict) -> dict:
    logger.info("=== Step 1：規劃 ===")
    site_map = harness.get("site_map", {})
    pages    = site_map.get("pages", {})
    memory   = harness.get("memory", {})

    # 過去設計決定摘要
    past_decisions = "; ".join(
        d.get("note", "") for d in memory.get("design_decisions", [])[-3:]
    )

    file_summary = []
    for fname, content in current_content.items():
        desc    = pages.get(fname, {}).get("description", "")
        preview = content[:PREVIEW_CHARS]
        tail    = f"...（共 {len(content)} 字元）" if len(content) > PREVIEW_CHARS else ""
        file_summary.append(f"[{fname}]{' — ' + desc if desc else ''}\n{preview}{tail}")

    raw = _llm([
        {"role": "system", "content": PLAN_SYSTEM},
        {"role": "user", "content": (
            f"使用者需求：{user_message}\n"
            f"過去設計決定：{past_decisions}\n\n"
            "現有檔案：\n" + "\n\n".join(file_summary)
        )},
    ], max_tokens=512)

    logger.info("Step1 規劃原始：%s", raw[:300])
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()
    match   = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if not match:
        raise ValueError(f"規劃階段無法解析 JSON：{raw[:200]}")

    plan = json.loads(match.group(0))

    # 過濾受保護檔案
    to_delete = plan.get("files_to_delete", [])
    skipped   = [f for f in to_delete if f in PROTECTED_FILES]
    plan["files_to_delete"]    = [f for f in to_delete if f not in PROTECTED_FILES]
    plan["_skipped_protected"] = skipped
    if skipped:
        logger.warning("受保護檔案已從刪除清單移除：%s", skipped)

    return plan

# ══════════════════════════════════════════════════════════════════════════════
# Step 2：逐檔生成（注入設計系統）
# ══════════════════════════════════════════════════════════════════════════════

GENERATE_SYSTEM = """
你是專業前端工程師。生成「單一檔案」的完整內容。

規則：
1. 只輸出該檔案的純文字內容，不要任何說明、markdown 或程式碼標記。
2. 內容必須完整，絕對不可省略或截斷。
3. 必須嚴格遵守【設計系統】的色彩、字體、規則。
4. HTML 頁面必須直接使用【固定元件模板】的 nav 和 hero 結構，不得自行重新設計。
5. 每個 HTML 頁面必須包含完整的 nav 和 footer。
6. HTML 標籤正確閉合，JS 括號配對完整。
""".strip()

def _step2_generate_file(
    filename: str,
    action: str,
    user_message: str,
    plan_summary: str,
    existing_content: str | None,
    context_files: dict,
    design_context: str,
    nav_items: str,
) -> str:
    logger.info("Step2 生成：%s（%s）", filename, action)

    context_text = ""
    if context_files:
        parts = [f"=== {k} ===\n{v}" for k, v in context_files.items()]
        context_text = "【參考現有檔案】\n" + "\n\n".join(parts) + "\n\n"

    nav_note = (
        "【導覽列 NAV_ITEMS 請替換為以下內容】\n"
        f"{nav_items}\n\n"
    )

    base = (
        f"{design_context}\n\n"
        f"{nav_note}"
        f"{context_text}"
    )

    if action == "update" and existing_content:
        task = (
            f"修改檔案：{filename}\n"
            f"使用者需求：{user_message}\n"
            f"此次目標：{plan_summary}\n\n"
            f"{base}"
            f"【現有 {filename} 內容】\n{existing_content}\n\n"
            f"輸出修改後的完整 {filename}（純內容，不要任何說明）："
        )
    else:
        task = (
            f"建立新檔案：{filename}\n"
            f"使用者需求：{user_message}\n"
            f"此次目標：{plan_summary}\n\n"
            f"{base}"
            f"輸出完整的 {filename}（純內容，不要任何說明）："
        )

    content = _llm([
        {"role": "system", "content": GENERATE_SYSTEM},
        {"role": "user",   "content": task},
    ], max_tokens=4096)

    # 清除可能的 markdown 包裝
    content = re.sub(r'^```[\w]*\n?', '', content.strip())
    content = re.sub(r'\n?```$', '', content.strip())
    return content.strip()

# ══════════════════════════════════════════════════════════════════════════════
# Step 3：驗證
# ══════════════════════════════════════════════════════════════════════════════

def _step3_validate(filename: str, content: str) -> list[str]:
    warnings = []
    if filename.endswith(".html"):
        if "<!DOCTYPE" not in content:
            warnings.append("缺少 <!DOCTYPE>")
        if "</html>" not in content:
            warnings.append("缺少 </html> 閉合標籤")
        if "site-header" not in content:
            warnings.append("未使用固定導覽列元件（缺少 site-header class）")
        if "site-footer" not in content:
            warnings.append("未使用固定頁腳元件（缺少 site-footer class）")
        if "#0A0A1A" not in content and "#6C63FF" not in content and "var(--" not in content:
            warnings.append("未套用設計系統色彩")
    if filename.endswith(".js"):
        opens  = content.count('{')
        closes = content.count('}')
        if abs(opens - closes) > 3:
            warnings.append(f"JS 大括號不配對（{{ {opens} / }} {closes}）")
    return warnings

# ══════════════════════════════════════════════════════════════════════════════
# Harness 狀態更新
# ══════════════════════════════════════════════════════════════════════════════

def _update_memory(harness: dict, user_message: str, plan: dict):
    memory = harness.get("memory", {})
    log    = memory.get("change_log", [])
    now    = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    log.append({
        "time":    now,
        "request": user_message[:100],
        "summary": plan.get("summary", ""),
        "files":   plan.get("files_to_update", []) + plan.get("files_to_create", []),
    })
    memory["change_log"] = log[-20:]

    note = plan.get("memory_note", "")
    if note:
        decisions = memory.get("design_decisions", [])
        decisions.append({"time": now, "note": note})
        memory["design_decisions"] = decisions[-10:]

    harness["memory"] = memory
    _save_harness("memory", memory)

def _update_site_map(harness: dict, plan: dict):
    site_map = harness.get("site_map", {})
    pages    = site_map.get("pages", {})

    for fname in plan.get("files_to_create", []):
        if fname.endswith(".html") and fname not in pages:
            title = fname.replace(".html", "").replace("-", " ").title()
            pages[fname] = {"title": title, "description": ""}

    for fname in plan.get("files_to_delete", []):
        pages.pop(fname, None)
        site_map["nav_links"] = [
            lnk for lnk in site_map.get("nav_links", [])
            if lnk.get("href") != fname
        ]

    if plan.get("nav_needs_update"):
        for fname in plan.get("files_to_create", []):
            if fname.endswith(".html"):
                existing_hrefs = [lnk["href"] for lnk in site_map.get("nav_links", [])]
                if fname not in existing_hrefs:
                    label = pages.get(fname, {}).get("title", fname.replace(".html", ""))
                    site_map.setdefault("nav_links", []).append({"label": label, "href": fname})

    site_map["pages"] = pages
    site_map["last_updated"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    harness["site_map"] = site_map
    _save_harness("site_map", site_map)

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

def handle_user_message(user_message: str) -> str:
    try:
        # 1. 讀取網站檔案
        files = list_website_files()
        current_content: dict[str, str] = {}
        for f in files:
            data = get_file_content(f)
            if data is not None:
                current_content[f] = data

        if not current_content:
            return "❌ 無法讀取任何網站檔案，請確認 GitHub 存取設定。"

        # 2. 讀取 Harness（設計系統 + 元件 + 地圖 + 記憶）
        logger.info("=== 讀取 Harness ===")
        harness  = _load_harness()
        site_map = harness.get("site_map", {})

        # 3. 意圖分類
        if _classify_intent(user_message) == "QUERY":
            return _answer_query(current_content, site_map)

        # 4. 組裝設計規範 context（注入每次 LLM 生成）
        design_context = _build_design_context(harness)
        nav_items      = _build_nav_items(site_map)

        # 5. Step 1：規劃
        plan = _step1_plan(user_message, current_content, harness)
        logger.info("規劃：更新%s 新建%s 刪除%s",
                    plan.get("files_to_update", []),
                    plan.get("files_to_create", []),
                    plan.get("files_to_delete", []))

        # 6. Step 2：逐檔生成（每個檔案獨立一次 LLM 呼叫，專注品質）
        logger.info("=== Step 2：逐檔生成 ===")
        plan_summary  = plan.get("summary", "")
        context_files = {
            f: current_content[f]
            for f in plan.get("context_files", [])
            if f in current_content
        }
        generated: dict[str, str] = {}

        all_filenames = plan.get("files_to_update", []) + plan.get("files_to_create", [])
        for fname in all_filenames:
            action = "update" if fname in plan.get("files_to_update", []) else "create"
            # 為每個頁面計算正確的 active nav item
            page_nav = _build_nav_items(site_map, current_page=fname)
            generated[fname] = _step2_generate_file(
                fname, action, user_message, plan_summary,
                current_content.get(fname),
                context_files, design_context, page_nav,
            )

        # 7. Step 3：驗證
        logger.info("=== Step 3：驗證 ===")
        all_warnings = []
        for fname, content in generated.items():
            warns = _step3_validate(fname, content)
            if warns:
                logger.warning("驗證警告 [%s]：%s", fname, warns)
                all_warnings.extend([f"{fname}: {w}" for w in warns])

        # 8. Step 4：寫入 GitHub
        logger.info("=== Step 4：寫入 ===")
        for fname, content in generated.items():
            logger.info("寫入：%s", fname)
            path = _strip_docs_prefix(fname)
            ok = update_or_create_file(path, content, f"AI 自動更新: {path}")
            if not ok:
                return f"❌ 寫入 {fname} 失敗，請檢查 GitHub 權限。"

        for fname in plan.get("files_to_delete", []):
            logger.info("刪除：%s", fname)
            delete_file(_strip_docs_prefix(fname), f"AI 自動刪除: {fname}")

        # 9. 更新 Harness 狀態（記憶 + 網站地圖）
        logger.info("=== 更新 Harness 狀態 ===")
        _update_memory(harness, user_message, plan)
        _update_site_map(harness, plan)

        # 10. 回覆
        updated = plan.get("files_to_update", []) + plan.get("files_to_create", [])
        deleted = plan.get("files_to_delete", [])
        skipped = plan.get("_skipped_protected", [])

        parts = [f"✅ 網站已更新！\n📋 {plan_summary}"]
        if updated:
            parts.append("📝 已更新：" + ", ".join(updated))
        if deleted:
            parts.append("🗑️ 已刪除：" + ", ".join(deleted))
        if skipped:
            parts.append("⚠️ 受保護未刪除：" + ", ".join(skipped))
        if all_warnings:
            parts.append("⚠️ 品質警告：" + "；".join(all_warnings))
        parts.append(f"🔗 {SITE_URL}")

        return "\n".join(parts)

    except Exception as e:
        logger.exception("處理訊息時發生嚴重錯誤")
        return f"❌ 系統發生錯誤：{e}"
