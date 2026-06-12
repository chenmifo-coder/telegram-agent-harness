import os
import base64
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO_OWNER = os.environ["REPO_OWNER"]
REPO_NAME = os.environ["REPO_NAME"]
BRANCH = "main"
WEBSITE_PATH = "website/"

def get_file_content(file_path):
    url = f"[https://api.github.com/repos/](https://api.github.com/repos/){REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    resp = requests.get(url, headers=headers)
    
    if resp.status_code == 200:
        content_b64 = resp.json()["content"]
        return base64.b64decode(content_b64).decode("utf-8")
    elif resp.status_code == 404:
        # 檔案不存在是正常的 (可能是 LLM 想要創建新檔案)
        return None
    else:
        print(f"❌ GitHub API Error (Get): {resp.status_code} - {resp.text}")
        return None

def update_or_create_file(file_path, content, commit_msg):
    url = f"[https://api.github.com/repos/](https://api.github.com/repos/){REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    
    # 先嘗試取得檔案的 SHA (更新現有檔案必填)
    resp = requests.get(url, headers=headers)
    sha = resp.json().get("sha") if resp.status_code == 200 else None
    
    data = {
        "message": commit_msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha
        
    put_resp = requests.put(url, headers=headers, json=data)
    
    if put_resp.status_code not in [200, 201]:
        print(f"❌ GitHub API Error (Put): {put_resp.status_code} - {put_resp.text}")
        return False
    return True

def list_website_files():
    url = f"[https://api.github.com/repos/](https://api.github.com/repos/){REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    resp = requests.get(url, headers=headers)
    
    if resp.status_code == 200:
        # 只抓取檔案，忽略子資料夾
        return [item["name"] for item in resp.json() if item["type"] == "file"]
    else:
        print(f"❌ GitHub API Error (List): {resp.status_code} - {resp.text}")
        return []
