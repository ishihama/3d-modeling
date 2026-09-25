"""1 モデル分のワークフローを通しで実行する（E2E 動作確認）。

    1. tools/export_model.py     … model.py → out/<name>.step / .stl
    2. tools/check_stl.py        … 印刷可能性チェック（ERROR 0 で合格）。複数パーツは各パーツを印刷の向きで
    3. build123d-mcp（.mcp.json と同じコマンドで起動し MCP プロトコルで呼ぶ）
       execute_file → validate → render_view ×4 … out/<name>-{front,side,top,iso}.png
    4. tools/viewer.py           … out/<name>-viewer.html（回せる 3D ビューア、単体で動く）

使い方:
    uv run tools/e2e.py models/<name> [--toy] [--dual] [--allow-supports] [--no-mcp]

終了コード: すべて成功で 0。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import check_stl  # noqa: E402
import export_model  # noqa: E402
import viewer  # noqa: E402

VIEWS = ["front", "side", "top", "iso"]


class McpClient:
    """最小限の MCP stdio クライアント（.mcp.json の build123d エントリを起動する）。"""

    def __init__(self) -> None:
        cfg = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["build123d"]
        self.proc = subprocess.Popen(
            [cfg["command"], *cfg["args"]], cwd=ROOT, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self.next_id = 0
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "e2e", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict) -> dict:
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("build123d-mcp が終了した")
            msg = json.loads(line)
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def call(self, tool: str, **args) -> tuple[bool, str]:
        res = self.request("tools/call", {"name": tool, "arguments": args})
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return not res.get("isError", False), text

    def close(self) -> None:
        self.proc.terminate()


def step(title: str) -> None:
    print(f"\n==== {title} ====")


def run_mcp(model_dir: Path, name: str) -> bool:
    mcp = McpClient()
    try:
        rel = model_dir.relative_to(ROOT) / "model.py"
        ok, text = mcp.call("execute_file", path=str(rel))
        loaded = ok and json.loads(text).get("ok", False)
        print(f"execute_file: {'OK' if loaded else 'NG'}")
        if not loaded:
            print(text)
            return False
        ok, text = mcp.call("execute", code=f"show(result, {name!r})")
        ok, text = mcp.call("validate", object_name=name)
        gate = ok and "Validity gate: PASS" in text
        print(f"validate: {'PASS' if gate else 'FAIL'}")
        if not gate:
            print(text)
        rendered = True
        for view in VIEWS:
            png = model_dir / "out" / f"{name}-{view}.png"
            ok, _ = mcp.call("render_view", objects=name, direction=view, save_to=str(png.relative_to(ROOT)))
            ok = ok and png.is_file()
            rendered &= ok
            print(f"render_view {view:5}: {png.relative_to(ROOT) if ok else 'NG'}")
        return gate and rendered
    finally:
        mcp.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="モデル 1 つ分の E2E（書き出し → チェック → MCP 検証・レンダリング）")
    ap.add_argument("model_dir", type=Path)
    ap.add_argument("--toy", action="store_true")
    ap.add_argument("--dual", action="store_true")
    ap.add_argument("--allow-supports", action="store_true")
    ap.add_argument("--no-mcp", action="store_true", help="build123d-mcp の検証・レンダリングを省く")
    args = ap.parse_args(argv)

    model_dir = args.model_dir.resolve()
    name = model_dir.name
    results: dict[str, bool] = {}

    step("1. export (model.py → STEP / STL)")
    results["export"] = export_model.main([str(model_dir)]) == 0

    if results["export"]:
        step("2. check_stl")
        opts = [f for f, on in (("--toy", args.toy), ("--dual", args.dual),
                                ("--allow-supports", args.allow_supports)) if on]
        ok = True
        for stl in export_model.printable_stls(model_dir):
            print(f"\n-- {stl.name}")
            ok &= check_stl.main([str(stl), *opts]) == 0
        results["check_stl"] = ok

    if not args.no_mcp:
        step("3. build123d-mcp (execute_file / validate / render_view)")
        results["mcp"] = run_mcp(model_dir, name)

    if results.get("export"):
        step("4. viewer")
        results["viewer"] = viewer.main([str(model_dir)]) == 0

    step("summary")
    for k, v in results.items():
        print(f"{k:10} {'OK' if v else 'NG'}")
    passed = all(results.values()) and "check_stl" in results
    print("E2E:", "PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
