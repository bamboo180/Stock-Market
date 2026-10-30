"""我的最愛與持倉（買進價）的儲存。

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
DEFAULT_INDICES = ["^TWII", "^TWOII", "^GSPC", "^IXIC", "^DJI", "^SOX"]
LOCAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favorites.json")


class StoreError(Exception):
    pass


DEFAULT_GROUP = "我的最愛"


def clean_groups(groups, extra=None) -> list[dict]:
    """群組清單：[{"name": 名稱, "codes": [代號…]}]。名稱不重複、每檔股票只屬於一個群組、順序保留。"""
    out, seen_code, seen_name = [], set(), set()
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        name = str(g.get("name") or "").strip() or DEFAULT_GROUP
        base, k = name, 2
        while name in seen_name:
            name, k = f"{base} {k}", k + 1
        seen_name.add(name)
        codes = []
        for c in _clean(g.get("codes")):
            if c not in seen_code:
                seen_code.add(c)
                codes.append(c)
        out.append({"name": name, "codes": codes})
    if not out:
        out = [{"name": DEFAULT_GROUP, "codes": []}]
    for c in _clean(extra):                      # 在最愛清單、但不在任何群組的 → 放到第一組
        if c not in seen_code:
            seen_code.add(c)
            out[0]["codes"].append(c)
    return out


def flatten(groups) -> list[str]:
    return [c for g in groups for c in g["codes"]]


def _normalize(data) -> dict:
    """舊格式（只有最愛清單、或沒有群組）自動轉成新格式。"""
    if isinstance(data, list):
        data = {"favorites": data}
    data = data if isinstance(data, dict) else {}
    pos = data.get("positions") or {}
    idx = data.get("indices")
    if data.get("groups"):
        groups = clean_groups(data["groups"])
    else:
        groups = clean_groups([{"name": DEFAULT_GROUP, "codes": data.get("favorites", DEFAULT)}])
    return {"favorites": flatten(groups), "groups": groups,
            "indices": _clean(DEFAULT_INDICES if idx is None else idx),
            "positions": {str(k).upper(): v for k, v in pos.items() if isinstance(v, dict) and v.get("cost")}}


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

    def load(self) -> dict:
        if not os.path.exists(LOCAL_PATH):
            return _normalize(None)
        try:
            with open(LOCAL_PATH, encoding="utf-8") as f:
                return _normalize(json.load(f))
        except Exception:
            return _normalize(None)

    def save(self, data: dict) -> None:
        try:
            with open(LOCAL_PATH, "w", encoding="utf-8") as f:
                json.dump(_normalize(data), f, ensure_ascii=False, indent=1)
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

    def load(self) -> dict:
        data, _ = self._file()
        return _normalize(data)

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

    def save(self, data: dict) -> None:
        self._ensure_branch()
        _, sha = self._file()
        body = {"message": "update favorites/positions", "branch": self.branch,
                "content": base64.b64encode(json.dumps(_normalize(data), ensure_ascii=False, indent=1).encode()).decode()}
        if sha:
            body["sha"] = sha
        r = self._req("PUT", f"/repos/{self.repo}/contents/{self.path}", json=body)
        if r.status_code not in (200, 201):
            raise StoreError(f"儲存最愛失敗（GitHub {r.status_code}）")


def make_store(github_token: str = "", github_repo: str = ""):
    if github_token and github_repo:
        return GitHubStore(github_token, github_repo)
    return LocalStore()
