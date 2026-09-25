"""models/<name>/model.py の build() を呼び、out/ に STEP と STL を書き出す。

使い方:
    uv run tools/export_model.py models/<name>

model.py は build123d-mcp のサンドボックスでも読めるよう、build123d 等だけを import し、
末尾で `result = build()` を定義する（ファイル操作はこのスクリプトが受け持つ）。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from build123d import export_step, export_stl

STL_TOLERANCE = 0.01          # mm（弦誤差）
STL_ANGULAR_TOLERANCE = 0.1   # rad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="model.py を STEP / STL に書き出す")
    ap.add_argument("model_dir", type=Path)
    args = ap.parse_args(argv)

    model_dir = args.model_dir.resolve()
    src = model_dir / "model.py"
    if not src.is_file():
        print(f"model.py が無い: {src}", file=sys.stderr)
        return 1
    spec = importlib.util.spec_from_file_location(f"model_{model_dir.name}", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    part = mod.result
    if not part.is_valid:
        print("ERROR: 不正なソリッド（is_valid = False）", file=sys.stderr)
        return 1

    name = model_dir.name
    out = model_dir / "out"
    out.mkdir(exist_ok=True)
    export_step(part, out / f"{name}.step")
    export_stl(part, out / f"{name}.stl", tolerance=STL_TOLERANCE, angular_tolerance=STL_ANGULAR_TOLERANCE)
    bb = part.bounding_box()
    print(f"volume {part.volume:.1f} mm³  bbox {bb.size.X:.2f} × {bb.size.Y:.2f} × {bb.size.Z:.2f} mm")
    print(f"wrote {out / name}.step, {out / name}.stl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
