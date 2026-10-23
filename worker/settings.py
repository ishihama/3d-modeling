"""ワーカーの設定（環境変数）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _read_token() -> str:
    path = os.environ.get("WORKER_GITHUB_TOKEN_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get("WORKER_GITHUB_TOKEN", "")


@dataclass(frozen=True)
class Settings:
    # モデルの状態を置く git リポジトリ（スレッドごとにブランチを切る）。既定はこのチェックアウト自身
    repo_remote: str = field(default_factory=lambda: _env("WORKER_REPO_REMOTE", str(ROOT)))
    base_branch: str = field(default_factory=lambda: _env("WORKER_BASE_BRANCH", "main"))
    branch_prefix: str = field(default_factory=lambda: _env("WORKER_BRANCH_PREFIX", "slack/"))
    push: bool = field(default_factory=lambda: _env("WORKER_PUSH", "0") == "1")
    work_root: Path = field(default_factory=lambda: Path(_env("WORKER_WORK_ROOT", "/tmp/3d-worker")))
    # GitHub（push の認証と PR 作成）。トークンはワーカー本体だけが使い、実行するコードには渡さない
    # トークンはファイルで渡すのを推奨（Cloud Run のシークレットのボリューム、モード 0400）。
    # 環境変数だと root のプロセス環境に残るため。WORKER_GITHUB_TOKEN_FILE が無ければ WORKER_GITHUB_TOKEN を使う
    github_token: str = field(default_factory=lambda: _read_token())
    github_repo: str = field(default_factory=lambda: _env("WORKER_GITHUB_REPO", "ishihama/3d-modeling"))
    github_api: str = field(default_factory=lambda: _env("WORKER_GITHUB_API", "https://api.github.com"))
    # 信頼できないコード（Claude が書いた build123d コード・model.py）を実行するユーザー。空なら切り替えない
    sandbox_user: str = field(default_factory=lambda: _env("WORKER_SANDBOX_USER", ""))
    # タイムアウト（秒）
    snippet_timeout: float = field(default_factory=lambda: float(_env("WORKER_SNIPPET_TIMEOUT", "120")))
    e2e_timeout: float = field(default_factory=lambda: float(_env("WORKER_E2E_TIMEOUT", "600")))
    git_timeout: float = field(default_factory=lambda: float(_env("WORKER_GIT_TIMEOUT", "120")))
    github_timeout: float = field(default_factory=lambda: float(_env("WORKER_GITHUB_TIMEOUT", "20")))
