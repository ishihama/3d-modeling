"""build123d のコード片を実行し、できた形状の寸法・体積・有効性を JSON で返す（3D ワーカーが隔離して実行する）。

使い方:
    python tools/run_snippet.py <code.py> <repo_root>

コードの最後に `result = ...`（形状）を置くか、`parts = {...}` を定義すると、それを測る。
どちらも無ければ、最後に作られた形状を測る。出力の最終行が `@@RESULT@@{json}`。
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path

MARKER = "@@RESULT@@"


def _measure(shape) -> dict:
    bb = shape.bounding_box()
    solids = shape.solids()
    return {
        "volume_mm3": round(float(shape.volume), 3),
        "bbox_mm": [round(bb.size.X, 3), round(bb.size.Y, 3), round(bb.size.Z, 3)],
        "bbox_min": [round(bb.min.X, 3), round(bb.min.Y, 3), round(bb.min.Z, 3)],
        "valid": bool(shape.is_valid),
        "solids": len(solids),
        "faces": len(shape.faces()),
    }


def main() -> int:
    code_path, root = Path(sys.argv[1]), Path(sys.argv[2])
    sys.path.insert(0, str(root))
    from build123d import Shape

    ns: dict = {"__name__": "__snippet__"}
    buf = io.StringIO()
    out: dict = {"ok": True}
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code_path.read_text(encoding="utf-8"), "<snippet>", "exec"), ns)
        shapes = {}
        if isinstance(ns.get("parts"), dict):
            shapes.update({f"parts.{k}": v for k, v in ns["parts"].items() if isinstance(v, Shape)})
        if isinstance(ns.get("result"), Shape):
            shapes["result"] = ns["result"]
        if not shapes:
            last = [(k, v) for k, v in ns.items() if isinstance(v, Shape) and not k.startswith("_")]
            if last:
                shapes[last[-1][0]] = last[-1][1]
        out["shapes"] = {k: _measure(v) for k, v in shapes.items()}
        if not shapes:
            out["note"] = "形状が見つからない（最後に result = ... を置く）"
    except Exception:
        out = {"ok": False, "error": "".join(traceback.format_exc(limit=8).splitlines(True)[-20:])}
    out["stdout"] = buf.getvalue()[-4000:]
    print(MARKER + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
