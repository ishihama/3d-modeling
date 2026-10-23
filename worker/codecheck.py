"""Claude が書いたコード（build123d の試し実行・model.py）の静的チェック。

CLAUDE.md の「model.py の import は build123d（と math / numpy 等）だけ」と同じ制限をかける。
build123d-mcp のサンドボックスと同じ考え方で、OS・ファイル・ネットワークに触る手段を止める。
これだけで完全に閉じ込められるわけではない（Python の静的チェックには抜け道がある）。
本当の境界は sandbox.py（権限を落とした実行）と、root が sandbox の書ける場所を読み書きしない作り（workspace.py）。
"""

from __future__ import annotations

import ast

ALLOWED_MODULES = {
    "build123d", "printlib", "math", "cmath", "numpy", "copy", "dataclasses", "typing",
    "itertools", "functools", "collections", "enum", "fractions", "decimal", "statistics",
    "operator", "string", "__future__",
}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview",
}
ALLOWED_DUNDERS = {"__name__", "__init__", "__post_init__", "__future__"}
# 許可したモジュールの属性をたどって OS などに届く経路（例: dataclasses.inspect.os）を止める
FORBIDDEN_ATTRS = {
    "os", "sys", "subprocess", "inspect", "builtins", "importlib", "socket", "shutil", "pathlib", "io",
    "ctypes", "ctypeslib", "posix", "pty", "signal", "multiprocessing", "threading", "tempfile", "glob",
    "pickle", "marshal", "modules", "system", "popen", "spawn", "fork", "execv", "execve",
    "f_globals", "f_locals", "f_back", "f_code", "gi_frame", "gi_code", "cr_frame", "ag_frame",
    "tb_frame", "tb_next", "func_globals", "load_library", "fromfile", "tofile", "save", "load",
    "savetxt", "loadtxt", "genfromtxt", "memmap",
}
MAX_SOURCE_BYTES = 100_000


def check_source(src: str) -> list[str]:
    """問題点のリスト（空なら OK）。"""
    if len(src.encode("utf-8")) > MAX_SOURCE_BYTES:
        return [f"コードが大きすぎる（{MAX_SOURCE_BYTES} バイトまで）"]
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"構文エラー: {e.msg}（{e.lineno} 行目）"]
    problems = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    problems.append(f"{line} 行目: import {alias.name} は使えない")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                problems.append(f"{line} 行目: from {'.' * node.level}{node.module or ''} import は使えない")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"{line} 行目: {node.id} は使えない")
        elif isinstance(node, ast.Attribute) and (
                node.attr in FORBIDDEN_ATTRS
                or (node.attr.startswith("_") and node.attr not in ALLOWED_DUNDERS)):
            problems.append(f"{line} 行目: .{node.attr} は使えない")
        elif isinstance(node, ast.Name) and node.id.startswith("__") and node.id not in ALLOWED_DUNDERS:
            problems.append(f"{line} 行目: {node.id} は使えない")
    allowed = ", ".join(sorted(m for m in ALLOWED_MODULES if m != "__future__"))
    if problems:
        problems.append(f"（import できるのは {allowed} だけ。ファイル・OS・ネットワークは触れない）")
    return problems
