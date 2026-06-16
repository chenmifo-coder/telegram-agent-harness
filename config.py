import os

# 環境變數
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
NVIDIA_API_KEY = os.environ["NVIDIA_API_KEY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO_OWNER = os.environ["REPO_OWNER"]
REPO_NAME = os.environ["REPO_NAME"]
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")

# 常數
BRANCH = "main"
WEBSITE_PATH = "docs"          # 網站檔案存放目錄
HARNESS_PATH = "harness"       # Harness 設定檔目錄（根目錄下）
SITE_URL = f"https://{REPO_OWNER}.github.io/{REPO_NAME}/"
PROTECTED_FILES = frozenset({"index.html", "style.css", ".nojekyll"})

# NVIDIA 模型設定
MODEL_NAME = os.getenv("AGENT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
MAX_RETRIES = int(os.getenv("AGENT_MAX_RETRIES", 2))
TEMPERATURE = float(os.getenv("AGENT_TEMPERATURE", 0.3))
PREVIEW_CHARS = int(os.getenv("PREVIEW_CHARS", 600))
