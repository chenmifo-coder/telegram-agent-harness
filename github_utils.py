import os
import base64
import requests
import logging
import time
from typing import Optional, List

logger = logging.getLogger(__name__)

# 從 config 導入
from config import GITHUB_TOKEN, REPO_OWNER, REPO_NAME, BRANCH, WEBSITE_PATH, HARNESS_PATH

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"}

def _api_url(file_path: str, subdir: Optional[str] = None) -> str:
    """構建 GitHub API URL，subdir 為 None 時使用 WEBSITE_PATH，為空字串時表示根目錄。"""
    if subdir is None:
        subdir = WEBSITE_PATH
    if subdir:
        path = f"{subdir}/{file_path}"
    else:
        path = file_path
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"

def _get_sha(file_path: str, subdir: Optional[str] = None) -> Optional[str]:
    resp = requests.get(_api_url(file_path, subdir), headers=HEADERS)
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None

def get_file_content(file_path: str, subdir: Optional[str] = None) -> Optional[str]:
    """取得檔案內容，subdir 為 None 時預設為 WEBSITE_PATH。"""
    resp = requests.get(_api_url(file_path, subdir), headers=HEADERS)
    if resp.status_code == 200:
        return base64.b64decode(resp.json()["content"]).decode("utf-8")
    elif resp.status_code == 404:
        return None
    logger.error("GitHub API Error (Get): %s - %s", resp.status_code, resp.text)
    return None

def update_or_create_file(file_path: str, content: str, commit_msg: str, subdir: Optional[str] = None) -> bool:
    """更新或新建檔案，subdir 預設為 WEBSITE_PATH。"""
    sha = _get_sha(file_path, subdir)
    data = {
        "message": commit_msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha

    resp = requests.put(_api_url(file_path, subdir), headers=HEADERS, json=data)
    if resp.status_code in [200, 201]:
        return True
    logger.error("GitHub API Error (Put): %s - %s", resp.status_code, resp.text)
    return False

def delete_file(file_path: str, commit_msg: Optional[str] = None, subdir: Optional[str] = None) -> bool:
    sha = _get_sha(file_path, subdir)
    if sha is None:
        logger.warning("刪除失敗：找不到檔案 %s（可能已不存在）", file_path)
        return False

    data = {
        "message": commit_msg or f"AI 自動刪除: {file_path}",
        "sha": sha,
        "branch": BRANCH,
    }
    resp = requests.delete(_api_url(file_path, subdir), headers=HEADERS, json=data)
    if resp.status_code == 200:
        logger.info("已刪除檔案：%s", file_path)
        return True
    logger.error("GitHub API Error (Delete): %s - %s", resp.status_code, resp.text)
    return False

def list_files(subdir: Optional[str] = None) -> List[str]:
    """列出子目錄下的所有檔案名稱，subdir 預設為 WEBSITE_PATH。"""
    if subdir is None:
        subdir = WEBSITE_PATH
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{subdir}"
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        return [item["name"] for item in resp.json() if item["type"] == "file"]
    elif resp.status_code == 404:
        logger.info("'%s' 目錄不存在，視為空目錄。", subdir)
        return []
    logger.error("GitHub API Error (List): %s - %s", resp.status_code, resp.text)
    return []
