"""3D ワーカーの HTTP API（FastAPI）。

起動: uv run --group worker uvicorn worker.app:app --port 8080
各エンドポイントは tools_spec.py のツールに 1 対 1 で対応する。thread ごとに作業場所（git ブランチ）を持つ。
ツール（tools/）と printlib はワーカー自身（イメージ）のものを使い、作業場所からは models/ だけを扱う。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import sys
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import github, sandbox
from .codecheck import check_source
from .settings import ROOT, Settings
from .tools_spec import TOOLS
from .workspace import Workspace, WorkspaceError, check_model_name, check_thread

READ_LIMIT = 200_000
SPEC_LIMIT = 50_000
ISSUES_PER_FILE = 10
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MEDIA_TYPES = {".png": "image/png", ".html": "text/html; charset=utf-8", ".stl": "model/stl",
               ".step": "application/step", ".json": "application/json"}

app = FastAPI(title="3d-modeling worker")
settings = Settings()
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _raise(e: WorkspaceError):
    raise HTTPException(status_code=e.status, detail=str(e))


@contextlib.contextmanager
def _run_area(ws: Workspace):
    """実行ごとの使い捨ての場所（runs/<乱数>/）と、その中の sandbox の HOME。終わったら消す。"""
    run_dir = ws.runs / uuid.uuid4().hex
    home = run_dir / "home"
    home.mkdir(parents=True)
    os.chmod(run_dir, 0o711)
    sandbox.give_to_sandbox(home, settings.sandbox_user)
    try:
        yield run_dir, home
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


async def _workspace(thread: str) -> Workspace:
    ws = Workspace(settings, thread)
    await ws.prepare()
    return ws


# --- リクエスト ---------------------------------------------------------------

class ThreadReq(BaseModel):
    thread: str


class ReadReq(ThreadReq):
    path: str


class CodeReq(ThreadReq):
    code: str


class WriteReq(ThreadReq):
    name: str
    spec_md: str | None = None
    model_py: str | None = None
    message: str = Field(min_length=1, max_length=500)


class E2EReq(ThreadReq):
    name: str
    toy: bool = False


class PRReq(ThreadReq):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(max_length=20_000)


# --- エンドポイント -------------------------------------------------------------

@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/v1/tools")
async def tools():
    """ボットに渡すツール定義と呼び出し先。"""
    return {"tools": TOOLS}


@app.post("/v1/read_file")
async def read_file(req: ReadReq):
    try:
        async with _locks[check_thread(req.thread)]:
            ws = await _workspace(req.thread)
            p = ws.resolve_readable(req.path)
            text = p.read_text(encoding="utf-8", errors="replace")
    except WorkspaceError as e:
        _raise(e)
    truncated = len(text) > READ_LIMIT
    return {"path": req.path, "content": text[:READ_LIMIT], "truncated": truncated}


@app.post("/v1/list_models")
async def list_models(req: ThreadReq):
    try:
        async with _locks[check_thread(req.thread)]:
            ws = await _workspace(req.thread)
            models = []
            for d in sorted((ws.repo / "models").iterdir()):
                if d.is_symlink() or not d.is_dir():
                    continue
                spec = d / "spec.md"
                title = None
                if spec.is_file() and not spec.is_symlink():
                    lines = spec.read_text(encoding="utf-8").splitlines()
                    title = lines[0].lstrip("# ") if lines else ""
                models.append({"name": d.name, "title": title, "has_model": (d / "model.py").is_file()})
    except WorkspaceError as e:
        _raise(e)
    return {"models": models}


@app.post("/v1/run_build123d")
async def run_build123d(req: CodeReq):
    problems = check_source(req.code)
    if problems:
        return {"ok": False, "rejected": problems}
    try:
        async with _locks[check_thread(req.thread)]:
            ws = await _workspace(req.thread)
            # 入力は root だけが書ける場所に置く（sandbox が仕込んだリンク越しに root が書かないように）
            code_file = ws.inputs / f"snippet-{uuid.uuid4().hex}.py"
            code_file.write_text(req.code, encoding="utf-8")
            os.chmod(code_file, 0o644)
            try:
                with _run_area(ws) as (_run_dir, home):
                    r = await sandbox.run(
                        [sys.executable, str(ROOT / "tools" / "run_snippet.py"), str(code_file), str(ROOT)],
                        cwd=home, env=sandbox.clean_env(home, ROOT), timeout=settings.snippet_timeout,
                        user=settings.sandbox_user)
            finally:
                code_file.unlink(missing_ok=True)
    except WorkspaceError as e:
        _raise(e)
    if r.timed_out:
        return {"ok": False, "error": f"{settings.snippet_timeout:g} 秒でタイムアウトした"}
    marker = [ln for ln in r.stdout.splitlines() if ln.startswith("@@RESULT@@")]
    if not marker:
        return {"ok": False, "error": "実行に失敗した", "stderr": r.stderr[-3000:]}
    return json.loads(marker[-1].removeprefix("@@RESULT@@"))


@app.post("/v1/write_model")
async def write_model(req: WriteReq):
    if req.spec_md is None and req.model_py is None:
        raise HTTPException(400, "spec_md と model_py のどちらかが要る")
    if req.spec_md is not None and len(req.spec_md) > SPEC_LIMIT:
        raise HTTPException(400, f"spec_md が大きすぎる（{SPEC_LIMIT} 文字まで）")
    if req.model_py is not None:
        problems = check_source(req.model_py)
        if problems:
            return {"ok": False, "rejected": problems}
    try:
        name = check_model_name(req.name)
        async with _locks[check_thread(req.thread)]:
            ws = await _workspace(req.thread)
            d = ws.model_dir(name)
            if d.is_symlink() or any((d / f).is_symlink() for f in ("spec.md", "model.py")):
                raise WorkspaceError(400, f"models/{name} にリンクがあるので書けない")
            d.mkdir(parents=True, exist_ok=True)
            written = []
            if req.spec_md is not None:
                (d / "spec.md").write_text(req.spec_md, encoding="utf-8")
                written.append(f"models/{name}/spec.md")
            if req.model_py is not None:
                (d / "model.py").write_text(req.model_py, encoding="utf-8")
                written.append(f"models/{name}/model.py")
            saved = await ws.save(written, f"{name}: {req.message}")
    except WorkspaceError as e:
        _raise(e)
    return {"ok": True, "written": written, **saved, "branch": ws.branch}


def _summarize_checks(out: Path, name: str) -> list[dict]:
    """root 側にコピーした成果物（artifacts/<name>/）からチェック結果をまとめる。

    中身は sandbox が作ったものなので信用しない（ファイル名を検証し、壊れていても落ちない）。
    """
    try:
        entries = json.loads((out / f"{name}.printables.json").read_text(encoding="utf-8"))["printables"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    rows = []
    for e in entries if isinstance(entries, list) else []:
        try:
            file, kind = str(e["file"]), str(e["kind"])
        except (KeyError, TypeError):
            continue
        if not FILENAME_RE.match(file) or not file.endswith(".stl"):
            continue
        try:
            c = json.loads((out / f"{file[:-4]}.check.json").read_text(encoding="utf-8"))
            issues = [f"[{i['level']}] {i['message']}" for i in c["checks"] if i["level"] in ("ERROR", "WARN")]
            rows.append({"file": file, "kind": kind, "errors": int(c["summary"]["errors"]),
                         "warnings": int(c["summary"]["warnings"]), "issues": issues[:ISSUES_PER_FILE]})
        except (OSError, ValueError, KeyError, TypeError):
            rows.append({"file": file, "kind": kind, "checked": False})
    return rows


@app.post("/v1/run_e2e")
async def run_e2e(req: E2EReq):
    try:
        name = check_model_name(req.name)
        async with _locks[check_thread(req.thread)]:
            ws = await _workspace(req.thread)
            src = ws.model_dir(name)
            if not (src / "model.py").is_file() or (src / "model.py").is_symlink():
                raise WorkspaceError(404, f"models/{name}/model.py が無い（先に modeling_write_model）")
            # 使い捨ての実行場所に写してから sandbox に渡す。root は以後この中を読まない（成果物は harvest で取り出す）
            with _run_area(ws) as (run_dir, home):
                model_dir = run_dir / name
                model_dir.mkdir()
                for f in ("spec.md", "model.py"):
                    if (src / f).is_file() and not (src / f).is_symlink():
                        shutil.copyfile(src / f, model_dir / f)
                # 親（run_dir）は root のまま。sandbox に渡すのはモデルのディレクトリだけ
                # （親まで渡すと <name> をリンクに差し替えられ、取り出し時に root がたどってしまう）
                sandbox.give_to_sandbox(model_dir, settings.sandbox_user)
                cmd = [sys.executable, str(ROOT / "tools" / "e2e.py"), str(model_dir), "--no-mcp", "--no-browser"]
                if req.toy:
                    cmd.append("--toy")
                r = await sandbox.run(cmd, cwd=home, env=sandbox.clean_env(home, ROOT),
                                      timeout=settings.e2e_timeout, user=settings.sandbox_user)
                out = ws.artifacts_dir(name)
                if out.exists():
                    shutil.rmtree(out)
                copied = sandbox.harvest(model_dir / "out", out, settings.sandbox_user)
    except WorkspaceError as e:
        _raise(e)
    if r.timed_out:
        return {"passed": False, "error": f"{settings.e2e_timeout:g} 秒でタイムアウトした"}
    return {
        "passed": r.code == 0,
        "checks": _summarize_checks(out, name),
        "artifacts": [f for f in copied if f.endswith((".png", ".html", ".stl"))],
        "log_tail": (r.stdout + ("\n" + r.stderr if r.code else ""))[-3000:],
    }


@app.get("/v1/artifacts/{thread}/{name}/{filename}")
async def artifact(thread: str, name: str, filename: str):
    try:
        ws = Workspace(settings, thread)
        path = ws.artifacts_dir(name) / filename
    except WorkspaceError as e:
        _raise(e)
    # artifacts/ は root がコピーした通常のファイルだけ（念のためリンクも拒否）
    if not FILENAME_RE.match(filename) or path.is_symlink() or not path.is_file():
        raise HTTPException(404, "ファイルが無い（このインスタンスで modeling_run_e2e を実行し直す）")
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
                        filename=filename)


@app.post("/v1/open_pr")
async def open_pr(req: PRReq):
    try:
        async with _locks[check_thread(req.thread)]:
            ws = await _workspace(req.thread)
            if settings.push:
                await ws.git("push", "--quiet", "origin", f"HEAD:refs/heads/{ws.branch}")
            pr = await github.open_pr(settings, ws.branch, req.title, req.body)
    except WorkspaceError as e:
        _raise(e)
    return {"ok": True, "branch": ws.branch, **pr}
