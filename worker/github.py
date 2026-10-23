"""GitHub の PR 作成（REST API）。"""

from __future__ import annotations

import httpx

from .settings import Settings
from .workspace import WorkspaceError


async def open_pr(s: Settings, branch: str, title: str, body: str,
                  transport: httpx.AsyncBaseTransport | None = None) -> dict:
    if not s.github_token:
        raise WorkspaceError(400, "WORKER_GITHUB_TOKEN が無いので PR を作れない")
    headers = {"Authorization": f"Bearer {s.github_token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    async with httpx.AsyncClient(base_url=s.github_api, headers=headers, timeout=s.github_timeout,
                                 transport=transport) as client:
        r = await client.post(f"/repos/{s.github_repo}/pulls",
                              json={"title": title, "head": branch, "base": s.base_branch, "body": body})
        if r.status_code == 201:
            return {"url": r.json()["html_url"], "created": True}
        if r.status_code == 422:      # 既に PR がある
            owner = s.github_repo.split("/")[0]
            existing = await client.get(f"/repos/{s.github_repo}/pulls",
                                        params={"head": f"{owner}:{branch}", "state": "open"})
            if existing.status_code == 200 and existing.json():
                return {"url": existing.json()[0]["html_url"], "created": False}
        raise WorkspaceError(502, f"PR の作成に失敗: HTTP {r.status_code} {r.text[:300]}")
