"""スレッドごとの作業場所（git のクローン + ブランチ <prefix><thread>）。

モデルの状態はブランチに置く（ワーカーのインスタンスは使い捨て）。会話の履歴は Slack のスレッドが持つ。
git の操作はワーカー本体（root）が行い、トークンは .git/config にも書かずコマンドごとに渡す。

ディレクトリと持ち主（sandbox が書ける場所を root が読み書きしないことが安全の要）:
    <base>/repo/       root だけ。git のクローン（sandbox には渡さない）
    <base>/in/         root だけ。sandbox に読ませる入力（試し実行のコード）
    <base>/runs/<id>/  実行ごとの使い捨ての場所（<id> は推測できない乱数）。root が作り、home/ と <name>/ だけを
                       sandbox に渡す。root は中を読まない（成果物は sandbox.harvest で取り出す）。終わったら消す
    <base>/artifacts/  root だけ。実行場所から安全にコピーした成果物（配信・集計はここから）
<base> と runs/ は 0711（中の一覧は見えないが通り抜けはできる）。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
from pathlib import Path

from .settings import Settings

THREAD_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


class WorkspaceError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def check_thread(thread: str) -> str:
    if not THREAD_RE.match(thread):
        raise WorkspaceError(400, "thread は英数字と . _ - の 80 文字まで")
    return thread


def check_model_name(name: str) -> str:
    if not MODEL_RE.match(name):
        raise WorkspaceError(400, "モデル名は英小文字・数字・ハイフン（例: slim-bin）")
    return name


class Workspace:
    def __init__(self, settings: Settings, thread: str):
        self.s = settings
        self.thread = check_thread(thread)
        self.base = settings.work_root / thread
        self.repo = self.base / "repo"
        self.inputs = self.base / "in"
        self.runs = self.base / "runs"
        self.artifacts = self.base / "artifacts"
        self.branch = f"{settings.branch_prefix}{thread}"

    # --- git ------------------------------------------------------------------

    def _auth_args(self) -> list[str]:
        if self.s.github_token and self.s.repo_remote.startswith("https://"):
            cred = base64.b64encode(f"x-access-token:{self.s.github_token}".encode()).decode()
            return ["-c", f"http.extraHeader=Authorization: Basic {cred}"]
        return []

    async def git(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        cmd = ["git", "-c", "safe.directory=*", *self._auth_args(), *args]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd or self.repo), env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), self.s.git_timeout)
        except TimeoutError:
            proc.kill()
            raise WorkspaceError(504, f"git {args[0]} がタイムアウトした") from None
        if check and proc.returncode != 0:
            msg = err.decode("utf-8", "replace").replace(self.s.github_token or "\0", "***")
            raise WorkspaceError(500, f"git {args[0]} に失敗: {msg.strip()[-500:]}")
        return out.decode("utf-8", "replace").strip()

    async def _remote_has_branch(self) -> bool:
        return bool(await self.git("ls-remote", "--heads", "origin", self.branch))

    async def prepare(self) -> None:
        """作業場所を用意する。初回はクローンしてスレッドのブランチに切り替え、以降はリモートの続きを取り込む。"""
        if not (self.repo / ".git").exists():
            if self.base.exists():
                shutil.rmtree(self.base)
            self.base.mkdir(parents=True)
            await self.git("clone", "--quiet", "--depth", "50", "--branch", self.s.base_branch,
                           self.s.repo_remote, str(self.repo), cwd=self.base)
            if await self._remote_has_branch():
                await self.git("fetch", "--quiet", "origin", f"{self.branch}:{self.branch}")
                await self.git("checkout", "--quiet", self.branch)
            else:
                await self.git("checkout", "--quiet", "-b", self.branch)
            os.chmod(self.repo / ".git", 0o700)       # sandbox ユーザーから git の中身を隠す
        elif await self._remote_has_branch():
            await self.git("fetch", "--quiet", "origin", self.branch)
            try:
                await self.git("merge", "--quiet", "--ff-only", "FETCH_HEAD")
            except WorkspaceError:
                raise WorkspaceError(409, "ブランチが別の場所で更新され、取り込めない") from None
        for d, mode in ((self.inputs, 0o755), (self.runs, 0o711), (self.artifacts, 0o755)):
            d.mkdir(exist_ok=True)
            os.chmod(d, mode)
        os.chmod(self.base, 0o711)

    async def save(self, paths: list[str], message: str) -> dict:
        """paths を commit し、設定があれば push する。"""
        await self.git("add", "--", *paths)
        if not await self.git("diff", "--cached", "--name-only"):
            return {"committed": False, "sha": await self.git("rev-parse", "HEAD"), "pushed": False}
        await self.git("-c", "user.name=3d-worker", "-c", "user.email=3d-worker@users.noreply.github.com",
                       "commit", "--quiet", "-m", message)
        sha = await self.git("rev-parse", "HEAD")
        pushed = False
        if self.s.push:
            await self.git("push", "--quiet", "origin", f"HEAD:refs/heads/{self.branch}")
            pushed = True
        return {"committed": True, "sha": sha, "pushed": pushed}

    # --- ファイル ---------------------------------------------------------------

    def model_dir(self, name: str) -> Path:
        return self.repo / "models" / check_model_name(name)

    def artifacts_dir(self, name: str) -> Path:
        return self.artifacts / check_model_name(name)

    def resolve_readable(self, rel: str) -> Path:
        """読んでよいファイル（リポジトリ内の文書・モデル・ライブラリ。out/ と .git は除く）。

        判定はたどった先のパスで行い、途中にリンクが 1 つでもあれば拒否する。
        """
        allowed_prefixes = ("models/", "printlib/", "templates/", "docs/", "tools/")
        allowed_files = {"CLAUDE.md", "README.md"}
        if rel not in allowed_files and not rel.startswith(allowed_prefixes):
            raise WorkspaceError(403, f"読めない場所: {rel}（{', '.join(sorted(allowed_files))} と "
                                      f"{', '.join(allowed_prefixes)} 以下だけ）")
        root = self.repo.resolve()
        p = root
        for part in Path(rel).parts:
            if part in ("..", ".git", "out") or part.startswith(".git"):
                raise WorkspaceError(403, f"読めない場所: {rel}")
            p = p / part
            if p.is_symlink():
                raise WorkspaceError(403, f"リンクは読めない: {rel}")
        if not p.resolve().is_relative_to(root):
            raise WorkspaceError(403, f"読めない場所: {rel}")
        if not p.is_file():
            raise WorkspaceError(404, f"ファイルが無い: {rel}")
        return p
