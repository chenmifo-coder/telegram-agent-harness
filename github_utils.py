import os
import base64
import requests
import logging

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO_OWNER   = os.environ["REPO_OWNER"]
REPO_NAME    = os.environ["REPO_NAME"]
BRANCH       = "main"
WEBSITE_PATH = "docs"

HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"}


#def _file_url(file_path: str) -> str:
#    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}/{file_path}"
def _file_url(file_path: str, is_root: bool = False) -> str:
    """
    產生 GitHub API 檔案路徑 URL。
    若 is_root 為 True，則忽略 WEBSITE_PATH，直接指向儲存庫根目錄。
    """
    if is_root:
        return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{file_path}"
    return f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}/{file_path}"

def _get_sha(file_path: str, is_root: bool = False) -> str | None:
    resp = requests.get(_file_url(file_path, is_root), headers=HEADERS)
    if resp.status_code == 200:
        return resp.json().get("sha")
    return None

def get_any_file_content(file_path: str) -> str | None:
    """取得根目錄的檔案內容"""
    resp = requests.get(_file_url(file_path, is_root=True), headers=HEADERS)
    if resp.status_code == 200:
        return base64.b64decode(resp.json()["content"]).decode("utf-8")
    elif resp.status_code == 404:
        return None
    logger.error("GitHub API Error (Get Any): %s - %s", resp.status_code, resp.text)
    return None

def get_file_content(file_path: str) -> str | None:
    resp = requests.get(_file_url(file_path), headers=HEADERS)
    if resp.status_code == 200:
        return base64.b64decode(resp.json()["content"]).decode("utf-8")
    elif resp.status_code == 404:
        return None
    logger.error("GitHub API Error (Get): %s - %s", resp.status_code, resp.text)
    return None


def update_or_create_file(file_path: str, content: str, commit_msg: str, is_root: bool = False) -> bool:
    sha = _get_sha(file_path, is_root)
    data = {
        "message": commit_msg,
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha

    resp = requests.put(_file_url(file_path, is_root), headers=HEADERS, json=data)
    if resp.status_code not in [200, 201]:
        logger.error("GitHub API Error (Put): %s - %s", resp.status_code, resp.text)
        return False
    return True

def delete_file(file_path: str, commit_msg: str | None = None) -> bool:
    """
    刪除 docs/ 底下的指定檔案。
    回傳 True 表示成功，False 表示失敗（包含檔案不存在）。
    """
    sha = _get_sha(file_path)
    if sha is None:
        logger.warning("刪除失敗：找不到檔案 %s（可能已不存在）", file_path)
        return False

    data = {
        "message": commit_msg or f"AI 自動刪除: {file_path}",
        "sha": sha,
        "branch": BRANCH,
    }
    resp = requests.delete(_file_url(file_path), headers=HEADERS, json=data)
    if resp.status_code == 200:
        logger.info("已刪除檔案：%s", file_path)
        return True

    logger.error("GitHub API Error (Delete): %s - %s", resp.status_code, resp.text)
    return False


def list_website_files() -> list[str]:
    resp = requests.get(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{WEBSITE_PATH}",
        headers=HEADERS,
    )
    if resp.status_code == 200:
        return [item["name"] for item in resp.json() if item["type"] == "file"]
    elif resp.status_code == 404:
        logger.info("'%s' 資料夾尚不存在，視為空目錄。", WEBSITE_PATH)
        return []
    logger.error("GitHub API Error (List): %s - %s", resp.status_code, resp.text)
    return []
