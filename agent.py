import os
import json
from openai import OpenAI
from github_utils import get_file_content, list_website_files, update_or_create_file

NVIDIA_API_KEY = os.environ["NVIDIA_API_KEY"]
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
)

SYSTEM_PROMPT = """
你是一位專業的前端開發工程師與網站設計師。你的任務是根據使用者的要求，修改公司網站的檔案。
公司網站目前位於 `website/` 資料夾，包含 HTML/CSS/JS 檔案。
你必須輸出一個嚴格符合以下格式的 JSON，**不可包含任何其他文字或標記**。請直接輸出 JSON，不要加 ```json ... ``` 包裝

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
現在開始。
"""

def process_user_request(user_message, current_files_content):
    """呼叫 NVIDIA LLM 產生檔案更新"""
    user_prompt = f"使用者要求：{user_message}\n\n目前網站檔案內容：\n{current_files_content}\n請輸出 JSON 更新。"
    response = client.chat.completions.create(
        model="meta/llama-3.1-70b-instruct",  # NVIDIA 免費模型
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.3,
        #response_format={"type": "json_object"}   # 強制 JSON 輸出
    )
    return json.loads(response.choices[0].message.content)

def apply_updates(updates):
    """將 LLM 產生的更新寫入 GitHub"""
    for item in updates["file_updates"]:
        path = item["path"]
        content = item["content"]
        success = update_or_create_file(path, content, f"AI 自動更新: {path}")
        if not success:
            return False, f"更新 {path} 失敗"
    return True, updates["reply_message"]

def handle_user_message(user_message):
    """外部呼叫的主流程"""
    # 1. 讀取現有網站所有檔案內容
    files = list_website_files()
    current_content = {}
    for f in files:
        current_content[f] = get_file_content(f) or ""
    files_text = "\n\n".join([f"=== {f} ===\n{content}" for f, content in current_content.items()])
    
    # 2. LLM 規劃更新
    updates = process_user_request(user_message, files_text)
    
    # 3. 套用更新
    ok, result = apply_updates(updates)
    if ok:
        return f"✅ 網站已更新！\n{result}\n🔗 https://{REPO_OWNER}.github.io/{REPO_NAME}/"
    else:
        return f"❌ 更新失敗：{result}"
