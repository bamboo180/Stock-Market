"""我的最愛的儲存。

兩種模式（自動選擇）：
1. GitHub 模式：Secrets 有 GITHUB_TOKEN 與 GITHUB_REPO 時，存在該 repo 的
   favorites-data 分支的 favorites.json。手機、電腦共用同一份，雲端重開也不會不見。
   用獨立分支是為了不觸發 Streamlit Cloud 重新部署。
2. 本機模式：存在 app 資料夾的 favorites.json。在自己電腦跑時用這個。
"""
from __future__ import annotations

import base64
import json
import os

import requests

DEFAULT = ["2330", "0050", "AAPL", "NVDA"]
LOCAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favorites.json")


class StoreError(Exception):
    pass


def _clean(items) -> list[str]:
    seen, out = set(), []
    for x in items or []:
        x = str(x).strip().upper()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


class LocalStore:
    label = "本機檔案 favorites.json"

    def load(self) -> list[str]:
        if not os.path.exists(LOCAL_PATH):
            return list(DEFAULT)
        try:
            with open(LOCAL_PATH, encoding="utf-8") as f:
                return _clean(json.load(f))
        except Exception:
            return list(DEFAULT)

    def save(self, favs: list[str]) -> None:
        try:
            with open(LOCAL_PATH, "w", encoding="utf-8") as f:
                json.dump(_clean(favs), f, ensure_ascii=False, indent=1)
        except OSError as e:
            raise StoreError(f"無法寫入 favorites.json：{e}") from e


class GitHubStore:
    API = "https://api.github.com"

    def __init__(self, token: str, repo: str, branch: str = "favorites-data", path: str = "favorites.json"):
        self.repo, self.branch, self.path = repo.strip().strip("/"), branch, path
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                  "X-GitHub-Api-Version": "2022-11-28"}
        self.label = f"GitHub（{self.repo} 的 {branch} 分支）"

    def _req(self, method: str, url: str, **kw):
        try:
            r = requests.request(method, self.API + url, headers=self.h, timeout=15, **kw)
        except Exception as e:
            raise StoreError(f"連不到 GitHub：{e}") from e
        if r.status_code in (401, 403):
            raise StoreError("GitHub token 沒有權限。請確認 token 有這個 repo 的 Contents: Read and write。")
        return r

    def _file(self):
        r = self._req("GET", f"/repos/{self.repo}/contents/{self.path}", params={"ref": self.branch})
        if r.status_code == 404:
            return None, None
        if r.status_code != 200:
            raise StoreError(f"讀取最愛失敗（GitHub {r.status_code}）")
        j = r.json()
        return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]

    def load(self) -> list[str]:
        data, _ = self._file()
        return list(DEFAULT) if data is None else _clean(data)

    def _ensure_branch(self):
        r = self._req("GET", f"/repos/{self.repo}/git/ref/heads/{self.branch}")
        if r.status_code == 200:
            return
        info = self._req("GET", f"/repos/{self.repo}")
        if info.status_code != 200:
            raise StoreError(f"找不到 repo {self.repo}，請檢查 GITHUB_REPO 設定（格式：帳號/repo名稱）")
        default = info.json()["default_branch"]
        base = self._req("GET", f"/repos/{self.repo}/git/ref/heads/{default}").json()["object"]["sha"]
        c = self._req("POST", f"/repos/{self.repo}/git/refs", json={"ref": f"refs/heads/{self.branch}", "sha": base})
        if c.status_code not in (200, 201):
            raise StoreError(f"建立 {self.branch} 分支失敗（GitHub {c.status_code}）")

    def save(self, favs: list[str]) -> None:
        self._ensure_branch()
        _, sha = self._file()
        body = {"message": "update favorites", "branch": self.branch,
                "content": base64.b64encode(json.dumps(_clean(favs), ensure_ascii=False, indent=1).encode()).decode()}
        if sha:
            body["sha"] = sha
        r = self._req("PUT", f"/repos/{self.repo}/contents/{self.path}", json=body)
        if r.status_code not in (200, 201):
            raise StoreError(f"儲存最愛失敗（GitHub {r.status_code}）")


def make_store(github_token: str = "", github_repo: str = ""):
    if github_token and github_repo:
        return GitHubStore(github_token, github_repo)
    return LocalStore()
