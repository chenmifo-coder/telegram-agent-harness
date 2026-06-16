"""
執行一次：初始化所有 Harness 設定檔並上傳到 GitHub。
用法：python harness_init.py
"""
import json
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from github_utils import update_or_create_file
from config import HARNESS_PATH

# ── 1. 設計系統 ───────────────────────────────────────────────────────────────
DESIGN_SYSTEM = {
    "style": "tech-dark",
    "description": "科技感深色風格，深藍/紫/霓虹色調",
    "colors": {
        "bg_primary":    "#0A0A1A",
        "bg_secondary":  "#12122A",
        "bg_card":       "#1A1A35",
        "accent":        "#6C63FF",
        "accent_glow":   "#8B80FF",
        "accent_neon":   "#00D4FF",
        "text_primary":  "#E8E8F0",
        "text_secondary":"#9898B0",
        "text_muted":    "#5A5A7A",
        "border":        "#2A2A4A",
        "success":       "#00E676",
        "warning":       "#FFB300",
        "error":         "#FF5252"
    },
    "fonts": {
        "heading": "Inter",
        "body":    "Noto Sans TC",
        "mono":    "JetBrains Mono",
        "cdn":     "<link rel='preconnect' href='https://fonts.googleapis.com'>\n<link href='https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&family=Noto+Sans+TC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap' rel='stylesheet'>"
    },
    "spacing": {
        "section_padding": "80px 0",
        "card_padding":    "24px",
        "border_radius":   "12px",
        "border_radius_lg":"20px"
    },
    "effects": {
        "card_shadow":  "0 4px 24px rgba(108,99,255,0.15)",
        "glow_accent":  "0 0 20px rgba(108,99,255,0.4)",
        "glow_neon":    "0 0 20px rgba(0,212,255,0.4)",
        "transition":   "all 0.3s ease",
        "glass":        "background: rgba(26,26,53,0.8); backdrop-filter: blur(12px)"
    },
    "style_rules": [
        "所有頁面背景色必須使用 bg_primary (#0A0A1A)",
        "主要強調色使用 accent (#6C63FF)，hover 時使用 accent_glow (#8B80FF)",
        "卡片使用 bg_card (#1A1A35) + border (#2A2A4A) + card_shadow",
        "標題字體 Inter，內文 Noto Sans TC",
        "按鈕：accent 背景，白色文字，hover 時 glow_accent 效果",
        "所有互動元素必須有 transition: all 0.3s ease",
        "霓虹裝飾色 accent_neon (#00D4FF) 只用於特別強調，不過度使用",
        "分隔線使用 border (#2A2A4A)",
        "所有頁面必須是響應式設計，手機版 max-width: 768px"
    ]
}

# ── 2. 元件模板 ───────────────────────────────────────────────────────────────
COMPONENTS = {
    "page_shell": {
        "description": "每個 HTML 頁面的基本骨架，{{TITLE}} {{EXTRA_HEAD}} {{BODY}} 為佔位符",
        "html": """<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{TITLE}} | FMTX AI 創新科技</title>
  <link rel="stylesheet" href="style.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&family=Noto+Sans+TC:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  {{EXTRA_HEAD}}
</head>
<body>
  {{NAV}}
  <main>
    {{BODY}}
  </main>
  {{FOOTER}}
</body>
</html>"""
    },
    "nav": {
        "description": "固定導覽列，{{NAV_ITEMS}} 替換為 <li> 清單",
        "html": """<header class="site-header">
  <nav class="navbar">
    <a class="nav-brand" href="index.html">
      <span class="brand-icon">⬡</span>
      <span class="brand-name">FMTX<span class="brand-accent"> AI</span></span>
    </a>
    <ul class="nav-links">
      {{NAV_ITEMS}}
    </ul>
    <button class="nav-toggle" aria-label="選單">☰</button>
  </nav>
</header>""",
        "css": """.site-header {
  position: sticky; top: 0; z-index: 100;
  background: rgba(10,10,26,0.85);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid #2A2A4A;
}
.navbar {
  max-width: 1200px; margin: 0 auto;
  padding: 0 24px; height: 64px;
  display: flex; align-items: center; gap: 32px;
}
.nav-brand {
  display: flex; align-items: center; gap: 10px;
  text-decoration: none; font-family: 'Inter', sans-serif;
  font-weight: 700; font-size: 1.25rem; color: #E8E8F0;
}
.brand-icon { color: #6C63FF; font-size: 1.5rem; }
.brand-accent { color: #6C63FF; }
.nav-links {
  display: flex; gap: 8px; list-style: none;
  margin: 0; padding: 0; margin-left: auto;
}
.nav-link {
  padding: 8px 16px; border-radius: 8px;
  text-decoration: none; color: #9898B0;
  font-family: 'Noto Sans TC', sans-serif; font-size: 0.9rem;
  transition: all 0.3s ease;
}
.nav-link:hover, .nav-link.active {
  color: #E8E8F0; background: rgba(108,99,255,0.15);
}
.nav-link.active { color: #6C63FF; }
.nav-toggle { display: none; background: none; border: none; color: #E8E8F0; font-size: 1.5rem; cursor: pointer; }
@media (max-width: 768px) {
  .nav-links { display: none; }
  .nav-toggle { display: block; }
}"""
    },
    "hero": {
        "description": "頁面頂部 Hero 區塊，{{HERO_TAG}} {{HERO_TITLE}} {{HERO_DESC}} {{HERO_CTA}} 為佔位符",
        "html": """<section class="hero">
  <div class="hero-bg"></div>
  <div class="container">
    <span class="hero-tag">{{HERO_TAG}}</span>
    <h1 class="hero-title">{{HERO_TITLE}}</h1>
    <p class="hero-desc">{{HERO_DESC}}</p>
    <div class="hero-cta">{{HERO_CTA}}</div>
  </div>
</section>""",
        "css": """.hero {
  position: relative; padding: 120px 0 80px;
  overflow: hidden; text-align: center;
}
.hero-bg {
  position: absolute; inset: 0;
  background: radial-gradient(ellipse 80% 60% at 50% 0%, rgba(108,99,255,0.2) 0%, transparent 70%);
  pointer-events: none;
}
.hero-tag {
  display: inline-block; padding: 6px 16px;
  background: rgba(108,99,255,0.15); border: 1px solid rgba(108,99,255,0.4);
  border-radius: 100px; color: #8B80FF;
  font-size: 0.85rem; font-family: 'Inter', sans-serif;
  letter-spacing: 0.05em; margin-bottom: 24px;
}
.hero-title {
  font-family: 'Inter', sans-serif; font-weight: 900;
  font-size: clamp(2.5rem, 6vw, 4.5rem); line-height: 1.1;
  color: #E8E8F0; margin: 0 0 24px;
}
.hero-title span { color: #6C63FF; }
.hero-desc {
  font-family: 'Noto Sans TC', sans-serif;
  font-size: 1.15rem; color: #9898B0; max-width: 600px;
  margin: 0 auto 40px; line-height: 1.8;
}
.hero-cta { display: flex; gap: 16px; justify-content: center; flex-wrap: wrap; }"""
    },
    "card": {
        "description": "通用卡片元件 CSS",
        "css": """.card {
  background: #1A1A35; border: 1px solid #2A2A4A;
  border-radius: 12px; padding: 24px;
  transition: all 0.3s ease;
}
.card:hover {
  border-color: rgba(108,99,255,0.5);
  box-shadow: 0 4px 24px rgba(108,99,255,0.15);
  transform: translateY(-2px);
}
.card-icon { font-size: 2rem; margin-bottom: 16px; }
.card-title {
  font-family: 'Inter', sans-serif; font-weight: 600;
  font-size: 1.1rem; color: #E8E8F0; margin: 0 0 8px;
}
.card-text {
  font-family: 'Noto Sans TC', sans-serif;
  color: #9898B0; font-size: 0.9rem; line-height: 1.7;
}"""
    },
    "button": {
        "description": "按鈕樣式 CSS",
        "css": """.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 12px 28px; border-radius: 8px; font-weight: 600;
  font-family: 'Inter', sans-serif; font-size: 0.95rem;
  text-decoration: none; cursor: pointer; border: none;
  transition: all 0.3s ease;
}
.btn-primary {
  background: #6C63FF; color: #fff;
}
.btn-primary:hover {
  background: #8B80FF; box-shadow: 0 0 20px rgba(108,99,255,0.4);
  transform: translateY(-1px);
}
.btn-outline {
  background: transparent; color: #E8E8F0;
  border: 1px solid #2A2A4A;
}
.btn-outline:hover {
  border-color: #6C63FF; color: #6C63FF;
  box-shadow: 0 0 20px rgba(108,99,255,0.2);
}"""
    },
    "footer": {
        "description": "固定頁腳",
        "html": """<footer class="site-footer">
  <div class="container">
    <div class="footer-brand">
      <span class="brand-icon">⬡</span>
      <span class="brand-name">FMTX<span class="brand-accent"> AI</span></span>
    </div>
    <p class="footer-desc">AI 驅動的下一代雲端解決方案</p>
    <p class="footer-copy">© 2026 FMTX AI 創新科技股份有限公司</p>
  </div>
</footer>""",
        "css": """.site-footer {
  border-top: 1px solid #2A2A4A; padding: 48px 0;
  text-align: center; margin-top: 80px;
}
.footer-brand {
  display: flex; align-items: center; justify-content: center;
  gap: 10px; font-family: 'Inter', sans-serif;
  font-weight: 700; font-size: 1.25rem; color: #E8E8F0;
  margin-bottom: 12px;
}
.footer-desc {
  color: #9898B0; font-family: 'Noto Sans TC', sans-serif;
  font-size: 0.9rem; margin: 0 0 8px;
}
.footer-copy { color: #5A5A7A; font-size: 0.8rem; margin: 0; }"""
    },
    "global_css": {
        "description": "全域 CSS reset 與 utility class",
        "css": """*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: #0A0A1A; color: #E8E8F0;
  font-family: 'Noto Sans TC', sans-serif;
  line-height: 1.6; min-height: 100vh;
}
.container { max-width: 1200px; margin: 0 auto; padding: 0 24px; }
section { padding: 80px 0; }
h1,h2,h3,h4 { font-family: 'Inter', sans-serif; font-weight: 700; color: #E8E8F0; }
h2 { font-size: clamp(1.8rem, 3vw, 2.5rem); }
h3 { font-size: 1.25rem; }
a { color: #6C63FF; }
.grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px,1fr)); gap: 24px; }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px,1fr)); gap: 24px; }
.text-accent { color: #6C63FF; }
.text-neon   { color: #00D4FF; }
.section-title { text-align: center; margin-bottom: 48px; }
.section-title h2 { margin-bottom: 12px; }
.section-title p  { color: #9898B0; font-size: 1rem; }"""
    }
}

# ── 3. 網站地圖 ───────────────────────────────────────────────────────────────
SITE_MAP = {
    "site_name": "FMTX AI 創新科技",
    "base_url": "https://chenmifo-coder.github.io/telegram-agent-harness/",
    "nav_links": [
        {"label": "首頁",     "href": "index.html"},
        {"label": "遊戲專區", "href": "games.html"},
        {"label": "定價方案", "href": "pricing.html"},
        {"label": "聯絡我們", "href": "contact.html"}
    ],
    "pages": {
        "index.html":   {"title": "首頁",     "description": "公司首頁，包含 Hero、服務介紹、數據統計"},
        "games.html":   {"title": "遊戲專區", "description": "小遊戲列表頁面"},
        "pricing.html": {"title": "定價方案", "description": "服務定價頁面"},
        "contact.html": {"title": "聯絡我們", "description": "聯絡表單頁面"},
        "style.css":    {"title": "樣式表",   "description": "全站共用 CSS"}
    },
    "last_updated": "2026-06-13"
}

# ── 4. 記憶初始化 ─────────────────────────────────────────────────────────────
MEMORY = {
    "initialized": "2026-06-13",
    "style": "tech-dark",
    "design_decisions": [
        {"note": "確定使用科技感深色風格，主色 #6C63FF，背景 #0A0A1A"},
        {"note": "字體：標題 Inter，內文 Noto Sans TC"},
        {"note": "固定元件：導覽列 nav、Hero 區塊"}
    ],
    "change_log": []
}

# ── 上傳到 GitHub ─────────────────────────────────────────────────────────────
FILES = {
    f"{HARNESS_PATH}/design_system.json": DESIGN_SYSTEM,
    f"{HARNESS_PATH}/components.json":    COMPONENTS,
    f"{HARNESS_PATH}/site_map.json":      SITE_MAP,
    f"{HARNESS_PATH}/memory.json":        MEMORY,
}

if __name__ == "__main__":
    print("初始化 Harness 設定檔...")
    for path, data in FILES.items():
        content = json.dumps(data, ensure_ascii=False, indent=2)
        ok = update_or_create_file(path, content, f"Harness 初始化: {path}", subdir="")
        print(f"{'✅' if ok else '❌'} {path}")
    print("\n完成！請確認 GitHub 倉庫根目錄下已產生 harness/ 資料夾。")
