import os
import base64
import requests

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO_OWNER = os.environ["REPO_OWNER"]
REPO_NAME = os.environ["REPO_NAME"]
BRANCH = "main"

# 將原本的 "website" 改為 "docs"
WEBSITE_PATH = "docs"

def get_file_content(file_path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}/{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    resp = requests.get(url, headers=headers)
    
    if resp.status_code == 200:
        content_b64 = resp.json()["content"]
        return base64.b64decode(content_b64).decode("utf-8")
    elif resp.status_code == 404:
        return None
    else:
        print(f"❌ GitHub API Error (Get): {resp.status_code} - {resp.text}")
        return None

def update_or_create_file(file_path, content, commit_msg):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}/{file_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    
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
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    resp = requests.get(url, headers=headers)
    
    if resp.status_code == 200:
        return [item["name"] for item in resp.json() if item["type"] == "file"]
    elif resp.status_code == 404:
        print(f"ℹ️ '{WEBSITE_PATH}' 資料夾尚不存在，視為空目錄。")
        return []
    else:
        print(f"❌ GitHub API Error (List): {resp.status_code} - {resp.text}")
        return []
