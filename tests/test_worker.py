"""3D ワーカーのテスト（pytest 不要。実物の git と build123d を使う通しのテスト）。

    uv run --group worker tests/test_worker.py

commit 済みの内容から一時的な git リポジトリを作り、その bare クローンをリモートにして、
スレッドのブランチへの保存 → 別インスタンスでの再開、import 制限、権限を落とした実行、PR 作成（モック）を確かめる。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from worker import app as app_module  # noqa: E402
from worker import github, sandbox  # noqa: E402
from worker.codecheck import check_source  # noqa: E402
from worker.settings import Settings  # noqa: E402
from worker.workspace import WorkspaceError  # noqa: E402

THREAD = "C0TEST-1730000000.000100"
SANDBOX_USER = "nobody" if os.geteuid() == 0 else ""

BOX_MODEL = '''from build123d import Box, Pos

from printlib import on_bed

result = on_bed(Pos(0, 0, 0) * Box(40, 30, 20))
'''
BOX_SPEC = "# test-box 仕様\n\n## 用途\n\nテスト用の箱\n\n## 対象年齢\n\n大人向け\n"


def _sh(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_remote(tmp: Path) -> Path:
    """commit 済みの内容（HEAD）を main に持つ bare リポジトリ。

    作業ツリーではなく HEAD を使うので、ワーカーが main に無いもの（未 commit のツール等）に頼っていると失敗する。
    """
    src = tmp / "src"
    src.mkdir()
    archive = subprocess.run(["git", "archive", "HEAD"], cwd=ROOT, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
    _sh("git", "init", "-q", "-b", "main", cwd=src)
    _sh("git", "add", "-A", cwd=src)
    _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "snapshot", cwd=src)
    bare = tmp / "remote.git"
    _sh("git", "clone", "-q", "--bare", str(src), str(bare))
    return bare


def make_settings(remote: Path, work_root: Path, **kw) -> Settings:
    work_root.mkdir(parents=True, exist_ok=True)
    os.chmod(work_root, 0o755)
    return Settings(repo_remote=str(remote), push=True, work_root=work_root, sandbox_user=SANDBOX_USER,
                    snippet_timeout=120, e2e_timeout=600, **kw)


# --- 単体 ---------------------------------------------------------------------

def test_codecheck_accepts_repo_models():
    for p in (ROOT / "models").glob("*/model.py"):
        assert check_source(p.read_text(encoding="utf-8")) == [], p


def test_codecheck_rejects_escapes():
    bad = {
        "import os": "import os",
        "from subprocess import run": "subprocess",
        "open('/etc/passwd')": "open",
        "x = ().__class__.__base__": "__class__",
        "__import__('os')": "__import__",
        "eval('1')": "eval",
        "from . import x": "from .",
        "import dataclasses\nos = dataclasses.inspect.os": ".inspect",      # レビューで見つかった抜け道
        "import numpy as np\nnp.save('x', 1)": ".save",
        "import copy\ncopy._reconstruct": "._reconstruct",
    }
    for src, needle in bad.items():
        problems = check_source(src)
        assert problems and any(needle in p for p in problems), (src, problems)


def test_tools_spec_shape():
    names = set()
    for t in app_module.TOOLS:
        d = t["definition"]
        assert d["name"].startswith("modeling_") and d["description"] and d["input_schema"]["type"] == "object"
        assert "thread" not in d["input_schema"]["properties"]   # thread はボットが付ける
        assert t["endpoint"].startswith("/v1/")
        names.add(d["name"])
    assert {"modeling_read_file", "modeling_run_build123d", "modeling_write_model", "modeling_run_e2e",
            "modeling_share_files", "modeling_open_pr"} <= names


def test_sandbox_drops_privileges_and_secrets():
    if not SANDBOX_USER:
        print("  (root ではないので権限の切り替えは確認しない)")
        return
    os.environ["WORKER_GITHUB_TOKEN_PROBE"] = "secret-value"
    with tempfile.TemporaryDirectory() as d:
        home = Path(d)
        os.chmod(home, 0o777)
        code = "import os; print(os.getuid(), 'secret-value' in str(os.environ))"
        r = asyncio.run(sandbox.run([sys.executable, "-c", code], cwd=home, env=sandbox.clean_env(home, ROOT),
                                    timeout=30, user=SANDBOX_USER))
    assert r.code == 0, r.stderr
    uid, leaked = r.stdout.split()
    assert uid == "65534" and leaked == "False", r.stdout


def test_sandbox_timeout_kills():
    with tempfile.TemporaryDirectory() as d:
        r = asyncio.run(sandbox.run([sys.executable, "-c", "while True: pass"], cwd=Path(d),
                                    env=sandbox.clean_env(Path(d), ROOT), timeout=2, user=""))
    assert r.timed_out


def test_sandbox_timeout_kills_daemonized_children():
    """二重 fork + setsid でプロセスグループから抜けた子がパイプを握っていても、タイムアウトで確実に戻り、子も止まる。"""
    if not SANDBOX_USER:
        print("  (root ではないので確認しない)")
        return
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        "    os.setsid()\n"
        "    if os.fork() == 0:\n"
        "        time.sleep(600)\n"
        "    os._exit(0)\n"
        "time.sleep(600)\n"
    )
    import time as _time
    started = _time.monotonic()
    with tempfile.TemporaryDirectory() as d:
        os.chmod(d, 0o777)
        r = asyncio.run(sandbox.run([sys.executable, "-c", code], cwd=Path(d), env=sandbox.clean_env(Path(d), ROOT),
                                    timeout=2, user=SANDBOX_USER))
    elapsed = _time.monotonic() - started
    assert r.timed_out and elapsed < 15, (r, elapsed)
    uid = __import__("pwd").getpwnam(SANDBOX_USER).pw_uid
    assert sandbox.kill_all(uid) == 0, "生き残りがいる"


def test_summarize_checks_tolerates_tampering():
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "m.printables.json").write_text('{"printables": [{"file": "../../etc/passwd"}, {"file": "a.stl",'
                                               ' "kind": "part"}, "x"]}', encoding="utf-8")
        (out / "a.check.json").write_text("not json", encoding="utf-8")
        rows = app_module._summarize_checks(out, "m")
        assert rows == [{"file": "a.stl", "kind": "part", "checked": False}], rows
        (out / "m.printables.json").write_text("garbage", encoding="utf-8")
        assert app_module._summarize_checks(out, "m") == []


def test_open_pr_mocked():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(422, json={"message": "A pull request already exists"})
        return httpx.Response(200, json=[{"html_url": "https://github.com/o/r/pull/7"}])

    s = Settings(github_token="t", github_repo="o/r")
    pr = asyncio.run(github.open_pr(s, "slack/x", "t", "b", transport=httpx.MockTransport(handler)))
    assert pr == {"url": "https://github.com/o/r/pull/7", "created": False}, pr
    assert calls == [("POST", "/repos/o/r/pulls"), ("GET", "/repos/o/r/pulls")]
    try:
        asyncio.run(github.open_pr(Settings(github_token=""), "b", "t", "b"))
    except WorkspaceError as e:
        assert e.status == 400
    else:
        raise AssertionError("トークンが無ければ 400")


# --- API を通しで ------------------------------------------------------------------

def test_api_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        os.chmod(tmp, 0o755)
        remote = make_remote(tmp)
        app_module.settings = make_settings(remote, tmp / "work-a")
        client = TestClient(app_module.app)

        r = client.post("/v1/read_file", json={"thread": THREAD, "path": "CLAUDE.md"})
        assert r.status_code == 200 and "Bambu Lab X2D" in r.json()["content"], r.text
        for bad in ("../etc/passwd", ".git/config", "models/desk-tray/out/x.stl", "pyproject.toml"):
            assert client.post("/v1/read_file", json={"thread": THREAD, "path": bad}).status_code in (403, 404), bad
        assert client.post("/v1/read_file", json={"thread": "../x", "path": "CLAUDE.md"}).status_code == 400

        names = [m["name"] for m in client.post("/v1/list_models", json={"thread": THREAD}).json()["models"]]
        assert "slim-bin" in names and "desk-tray" in names, names

        # 試し実行: 体積と外形
        r = client.post("/v1/run_build123d", json={"thread": THREAD, "code": BOX_MODEL}).json()
        assert r["ok"] and r["shapes"]["result"]["volume_mm3"] == 24000.0, r
        assert r["shapes"]["result"]["bbox_mm"] == [40.0, 30.0, 20.0]
        # 禁止された import は実行前に弾く
        r = client.post("/v1/run_build123d", json={"thread": THREAD, "code": "import os\nos.system('id')"}).json()
        assert r["ok"] is False and r["rejected"], r
        # 例外は traceback を返す
        r = client.post("/v1/run_build123d", json={"thread": THREAD, "code": "x = 1 / 0"}).json()
        assert r["ok"] is False and "ZeroDivisionError" in r["error"], r

        # 保存 → スレッドのブランチに commit・push
        r = client.post("/v1/write_model", json={"thread": THREAD, "name": "test-box", "spec_md": BOX_SPEC,
                                                 "model_py": BOX_MODEL, "message": "初版"}).json()
        assert r["ok"] and r["committed"] and r["pushed"] and r["branch"] == f"slack/{THREAD}", r
        heads = _sh("git", "ls-remote", "--heads", str(remote))
        assert f"refs/heads/slack/{THREAD}" in heads and r["sha"] in heads
        bad = client.post("/v1/write_model", json={"thread": THREAD, "name": "test-box",
                                                   "model_py": "import socket", "message": "x"}).json()
        assert bad["ok"] is False and bad["rejected"]
        assert client.post("/v1/write_model", json={"thread": THREAD, "name": "../evil",
                                                    "spec_md": "x", "message": "x"}).status_code == 400

        # 通し確認（権限を落として実行）
        r = client.post("/v1/run_e2e", json={"thread": THREAD, "name": "test-box"}).json()
        assert r["passed"], r["log_tail"]
        assert r["checks"] and r["checks"][0]["errors"] == 0, r["checks"]
        for f in ("test-box-iso.png", "test-box-viewer.html", "test-box.stl"):
            assert f in r["artifacts"], r["artifacts"]
        if SANDBOX_USER:
            ws = app_module.Workspace(app_module.settings, THREAD)
            # 成果物は root 側にコピーされている（取り出すのは sandbox の持ち物だけなので、ある＝ sandbox で動いた）
            assert (ws.artifacts_dir("test-box") / "test-box.stl").stat().st_uid == 0
            assert not (ws.model_dir("test-box") / "out").exists()      # リポジトリには何も書かれない
            # 実行するコードからは .git（トークンが入りうる場所）を読めない
            probe = asyncio.run(sandbox.run(
                [sys.executable, "-c", f"open({str(ws.repo / '.git' / 'config')!r}).read()"],
                cwd=Path("/"), env=sandbox.clean_env(Path("/tmp"), ws.repo), timeout=30, user=SANDBOX_USER))
            assert probe.code != 0 and "PermissionError" in probe.stderr, probe.stderr
        a = client.get(f"/v1/artifacts/{THREAD}/test-box/test-box-iso.png")
        assert a.status_code == 200 and a.content[:4] == b"\x89PNG"
        assert client.get(f"/v1/artifacts/{THREAD}/test-box/..%2F..%2Fspec.md").status_code == 404
        assert client.get(f"/v1/artifacts/{THREAD}/test-box/nothing.stl").status_code == 404
        assert list(app_module.Workspace(app_module.settings, THREAD).runs.iterdir()) == []   # 実行場所は消えている

        # 別インスタンス（作業場所が空）でも、リモートのブランチから続きを開ける
        app_module.settings = make_settings(remote, tmp / "work-b")
        client_b = TestClient(app_module.app)
        names = [m["name"] for m in client_b.post("/v1/list_models", json={"thread": THREAD}).json()["models"]]
        assert "test-box" in names, names
        r = client_b.post("/v1/read_file", json={"thread": THREAD, "path": "models/test-box/spec.md"}).json()
        assert r["content"] == BOX_SPEC
        # 別スレッドからは見えない
        names = [m["name"] for m in client_b.post("/v1/list_models", json={"thread": "OTHER-1"}).json()["models"]]
        assert "test-box" not in names


# --- 攻撃の再現（import の検査をすり抜けた前提でも、隔離で守れること） ------------------------

def _bypass_codecheck():
    original = app_module.check_source
    app_module.check_source = lambda src: []
    return original


def test_sandbox_holds_even_if_codecheck_is_bypassed():
    if not SANDBOX_USER:
        print("  (root ではないので確認しない)")
        return
    os.environ["WORKER_FAKE_SECRET"] = "fake-secret-value"
    original = _bypass_codecheck()
    try:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            os.chmod(tmp, 0o755)
            remote = make_remote(tmp)
            app_module.settings = make_settings(remote, tmp / "work")
            client = TestClient(app_module.app)
            ws = app_module.Workspace(app_module.settings, THREAD)
            pid = os.getpid()

            # 1. 試し実行から: root の環境・.git・リポジトリへの書き込み・入力ファイル置き場への書き込み
            probe = f"""
import os
results = {{}}
for label, fn in [
    ("environ", lambda: open('/proc/{pid}/environ', 'rb').read()),
    ("gitconfig", lambda: open({str(ws.repo / '.git' / 'config')!r}).read()),
    ("write_repo", lambda: open({str(ws.repo / 'models' / 'pwn.txt')!r}, 'w').write('x')),
    ("symlink_repo", lambda: os.symlink('/proc/{pid}/environ', {str(ws.repo / 'models' / 'evil')!r})),
    ("write_inputs", lambda: open({str(ws.inputs / 'x.py')!r}, 'w').write('x')),
]:
    try:
        fn()
        results[label] = "OK"
    except Exception as e:
        results[label] = type(e).__name__
print(results)
"""
            r = client.post("/v1/run_build123d", json={"thread": THREAD, "code": probe}).json()
            assert r["ok"], r
            got = eval(r["stdout"].strip())
            assert all(v != "OK" for v in got.values()), got
            assert not (ws.repo / "models" / "pwn.txt").exists() and not (ws.repo / "models" / "evil").exists()

            # 2. model.py から: 成果物の場所に root の環境へのリンクを仕込む・親を差し替える
            evil_model = f"""
import os
from build123d import Box
out = os.path.join(os.path.dirname(__file__), 'out')
os.makedirs(out, exist_ok=True)
os.symlink('/proc/{pid}/environ', os.path.join(out, 'leak.png'))
os.symlink({str(ws.repo / '.git' / 'config')!r}, os.path.join(out, 'git.html'))
try:
    run_dir = os.path.dirname(os.path.dirname(__file__))
    os.rename(os.path.dirname(__file__), os.path.join(run_dir, 'moved'))
except OSError:
    pass
result = Box(20, 20, 10)
"""
            w = client.post("/v1/write_model", json={"thread": THREAD, "name": "evil-box", "spec_md": "# x\n",
                                                     "model_py": evil_model, "message": "x"}).json()
            assert w["ok"], w
            e = client.post("/v1/run_e2e", json={"thread": THREAD, "name": "evil-box"}).json()
            assert "leak.png" not in e["artifacts"] and "git.html" not in e["artifacts"], e["artifacts"]
            for f in ("leak.png", "git.html"):
                a = client.get(f"/v1/artifacts/{THREAD}/evil-box/{f}")
                assert a.status_code == 404 and b"fake-secret-value" not in a.content, f
            for p in ws.artifacts.rglob("*"):
                assert not p.is_symlink(), p
                if p.is_file():
                    assert b"fake-secret-value" not in p.read_bytes(), p
            listing = client.post("/v1/list_models", json={"thread": THREAD}).text
            assert "fake-secret-value" not in listing
    finally:
        app_module.check_source = original
        os.environ.pop("WORKER_FAKE_SECRET", None)


def test_read_file_refuses_links_and_git():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        os.chmod(tmp, 0o755)
        remote = make_remote(tmp)
        app_module.settings = make_settings(remote, tmp / "work")
        client = TestClient(app_module.app)
        ws = app_module.Workspace(app_module.settings, THREAD)
        client.post("/v1/list_models", json={"thread": THREAD})     # 作業場所を用意
        (ws.repo / "models" / "glink").symlink_to(ws.repo / ".git")   # （root しか作れないが、念のための判定）
        for path in ("models/glink/config", "models/glink", "docs/../.git/config", "models/desk-tray/out/a.stl"):
            r = client.post("/v1/read_file", json={"thread": THREAD, "path": path})
            assert r.status_code in (403, 404) and "[core]" not in r.text, (path, r.text)


def main() -> int:
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
