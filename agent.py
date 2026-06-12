import os
import json
import re
from openai import OpenAI
from github_utils import get_file_content, list_website_files, update_or_create_file, REPO_OWNER, REPO_NAME

NVIDIA_API_KEY = os.environ["NVIDIA_API_KEY"]

client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
    timeout=600.0,
    max_retries=3
)

# 這裡也將提示詞中的 website/ 改成 docs/
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
"""

def process_user_request(user_message, current_files_content):
    user_prompt = f"使用者要求：{user_message}\n\n目前網站檔案內容：\n{current_files_content}\n請輸出 JSON 更新。"
    
    try:
        response = client.chat.completions.create(
            model="meta/llama-3.1-70b-instruct", 
            #model="nvidia/nemotron-3-super-120b-a12b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
        )
        content = response.choices[0].message.content
        
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
        else:
            # 2. 如果沒有標記，再退回尋找大括號 {}
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                json_str = match.group(0).strip()
            else:
                raise ValueError("無法從 LLM 回覆中找到有效的 JSON")
                
        return json.loads(json_str)
            
    except json.JSONDecodeError as e:
        print(f"JSON 解析錯誤: {str(e)}\n原始內容: {content}")
        # 將錯誤訊息具體化，回傳給使用者
        raise ValueError("AI 產生的程式碼中有未跳脫的雙引號，或內容太長被截斷，導致格式損毀。請嘗試縮小要求範圍（例如：只要求修改 HTML）。")
    except Exception as e:
        raise Exception(f"AI 處理請求時發生錯誤: {str(e)}")

def apply_updates(updates):
    for item in updates.get("file_updates", []):
        path = item["path"]
        content = item["content"]
        success = update_or_create_file(path, content, f"AI 自動更新: {path}")
        if not success:
            return False, f"更新 {path} 失敗，請檢查 GitHub 權限或 API 限制。"
    return True, updates.get("reply_message", "未提供修改說明")

def handle_user_message(user_message):
    try:
        # 1. 讀取現有網站所有檔案內容
        files = list_website_files()
        current_content = {}
        for f in files:
            file_data = get_file_content(f)
            if file_data is not None:
                current_content[f] = file_data
                
        files_text = "\n\n".join([f"=== {f} ===\n{content}" for f, content in current_content.items()])
        
        # 2. LLM 規劃更新
        updates = process_user_request(user_message, files_text)
        
        # 3. 套用更新
        ok, result = apply_updates(updates)
        if ok:
            return f"✅ 網站已更新！\n{result}\n🔗 https://{REPO_OWNER}.github.io/{REPO_NAME}/"
        else:
            return f"❌ 更新失敗：{result}"
            
    except Exception as e:
        print(f"處理訊息時發生嚴重錯誤: {e}")
        return f"❌ 系統發生錯誤：{str(e)}"
