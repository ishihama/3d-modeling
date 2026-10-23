"""信頼できないコードの実行（権限の低いユーザー・秘密情報なしの環境変数・時間とプロセス数の上限）。"""

from __future__ import annotations

import asyncio
import os
import pwd
import resource
import shutil
import signal
import stat
import time
import weakref
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_PROCS = 256                      # fork 爆弾よけ
MAX_FILE_BYTES = 512 * 1024 * 1024   # 1 ファイルの上限（STL・STEP が収まる大きさ）
OUTPUT_LIMIT = 200_000               # 返す stdout / stderr の上限（文字）


@dataclass
class RunResult:
    code: int | None
    stdout: str
    stderr: str
    timed_out: bool


def _sandbox_ids(user: str) -> tuple[int, int] | None:
    if not user or os.geteuid() != 0:
        return None
    pw = pwd.getpwnam(user)
    return pw.pw_uid, pw.pw_gid


def give_to_sandbox(path: Path, user: str) -> None:
    """path（以下すべて）を sandbox ユーザーの持ち物にする。リンクはたどらない（リンク自体の持ち主だけ変える）。

    root が作ったばかりの、sandbox がまだ触っていないディレクトリにだけ使う。
    """
    ids = _sandbox_ids(user)
    if ids is None:
        return
    os.chown(path, *ids, follow_symlinks=False)
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        for n in dirnames + filenames:
            os.chown(os.path.join(dirpath, n), *ids, follow_symlinks=False)


def harvest(src_dir: Path, dst_dir: Path, user: str, max_bytes: int = MAX_FILE_BYTES) -> list[str]:
    """sandbox が作ったファイルを、root 側の dst_dir に安全にコピーする（直下のファイルだけ）。

    コピーするのは「通常のファイル・リンクではない・ハードリンクではない・sandbox ユーザーの持ち物」だけ。
    sandbox が /proc/self/environ や .git/config へのリンクを仕込んでも、root がそれをたどって読むことはない。
    """
    ids = _sandbox_ids(user)
    copied = []
    dst_dir.mkdir(parents=True, exist_ok=True)
    try:
        dfd = os.open(src_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return copied
    try:
        for name in sorted(os.listdir(dfd)):
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except OSError:          # リンク・権限なしなど
                continue
            try:
                st = os.fstat(fd)
                if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > max_bytes
                        or (ids is not None and st.st_uid != ids[0])):
                    continue
                with open(dst_dir / name, "wb") as out, os.fdopen(os.dup(fd), "rb") as src:
                    shutil.copyfileobj(src, out)
                copied.append(name)
            finally:
                os.close(fd)
    finally:
        os.close(dfd)
    return copied


def clean_env(home: Path, pythonpath: Path) -> dict[str, str]:
    """秘密情報を含まない最小限の環境変数。"""
    venv_bin = str(Path(sys.executable).parent)
    return {
        "PATH": f"{venv_bin}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(home),
        "MPLCONFIGDIR": str(home),
        "PYTHONPATH": str(pythonpath),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",            # ~/.local の site-packages を読まない（仕込み対策）
        "LANG": "C.UTF-8",
    }


# 実行は全体で 1 つずつ（同じ sandbox ユーザーのプロセスが同時に存在しないようにし、終了後に全部止められるように）
_exec_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()
POST_KILL_READ_TIMEOUT = 5.0


def _exec_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _exec_locks:
        _exec_locks[loop] = asyncio.Lock()
    return _exec_locks[loop]


def kill_all(uid: int) -> int:
    """uid のプロセスをすべて止める（二重 fork + setsid でプロセスグループから抜けた子も含む）。止めた数を返す。"""
    killed = 0
    for _ in range(50):
        found = []
        for d in os.listdir("/proc"):
            if not d.isdigit() or int(d) == os.getpid():
                continue
            try:
                with open(f"/proc/{d}/status", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("Uid:"):
                            if int(line.split()[1]) == uid:
                                found.append(int(d))
                            break
            except OSError:
                continue
        if not found:
            return killed
        for pid in found:
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except ProcessLookupError:
                pass
        time.sleep(0.05)
    return killed


async def run(cmd: list[str], *, cwd: Path, env: dict[str, str], timeout: float, user: str) -> RunResult:
    ids = _sandbox_ids(user)
    cpu = int(timeout) + 5

    def limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCS, MAX_PROCS))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    kwargs = {}
    if ids is not None:
        kwargs.update(user=ids[0], group=ids[1], extra_groups=[])
    async with _exec_lock():
        if ids is not None:
            kill_all(ids[0])            # 前の実行の生き残りがいれば止める
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, preexec_fn=limits, **kwargs,
        )
        timed_out = False
        out = err = b""
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        finally:
            # 終わったら（正常終了でも）sandbox ユーザーのプロセスを全部止める。
            # setsid で抜けた子がパイプを握ったまま残ると読み取りが終わらず、後の実行にも干渉するため
            if ids is not None:
                kill_all(ids[0])
        if timed_out:
            try:
                out, err = await asyncio.wait_for(proc.communicate(), POST_KILL_READ_TIMEOUT)
            except TimeoutError:     # sandbox ユーザーを使わない環境（開発用）で子が残った場合
                out, err = b"", b""
                if proc.returncode is None:
                    proc.kill()
    return RunResult(proc.returncode, out.decode("utf-8", "replace")[-OUTPUT_LIMIT:],
                     err.decode("utf-8", "replace")[-OUTPUT_LIMIT:], timed_out)
