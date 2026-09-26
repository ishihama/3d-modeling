"""models/<name>/model.py の build() を呼び、out/ に STEP と STL を書き出す。

使い方:
    uv run tools/export_model.py models/<name>

model.py は build123d-mcp のサンドボックスでも読めるよう、build123d 等だけを import し、
末尾で `result = build()` を定義する（ファイル操作はこのスクリプトが受け持つ）。

複数パーツのモデルは、さらに `parts = {"<part>": Part, ...}` を「印刷の向き」で定義する。
    out/<name>.step / .stl          … result（組み立て状態。ビューア・確認用）
    out/<name>-<part>.step / .stl   … 各パーツ（印刷用。check_stl の対象）

試し刷りクーポンがあれば `coupons = {"<名前>": 形状（印刷の向き）}` を定義する（printlib.coupon を参照）。
    out/<name>-coupon-<名前>.step / .stl … 試し刷り用（check_stl の対象）
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from build123d import export_step, export_stl

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))   # model.py が printlib を import できるように

STL_TOLERANCE = 0.01          # mm（弦誤差）
STL_ANGULAR_TOLERANCE = 0.1   # rad


def load(model_dir: Path):
    src = model_dir / "model.py"
    spec = importlib.util.spec_from_file_location(f"model_{model_dir.name}", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def printable_stls(model_dir: Path) -> list[Path]:
    """印刷用 STL（check_stl の対象）のパス一覧。"""
    model_dir = model_dir.resolve()
    name, out = model_dir.name, model_dir / "out"
    parts = sorted(out.glob(f"{name}-*.stl"))
    parts = [p for p in parts if not p.stem.endswith("-viewer")]
    return parts or [out / f"{name}.stl"]


def _export(shape, path_base: Path) -> None:
    # with_suffix は名前の中のドット（例: snap0.3）を拡張子とみなすので、文字列で連結する
    export_step(shape, path_base.parent / f"{path_base.name}.step")
    export_stl(shape, path_base.parent / f"{path_base.name}.stl",
               tolerance=STL_TOLERANCE, angular_tolerance=STL_ANGULAR_TOLERANCE)
    bb = shape.bounding_box()
    print(f"{path_base.name:24} volume {shape.volume:10.1f} mm³  "
          f"bbox {bb.size.X:.2f} × {bb.size.Y:.2f} × {bb.size.Z:.2f} mm")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="model.py を STEP / STL に書き出す")
    ap.add_argument("model_dir", type=Path)
    args = ap.parse_args(argv)

    model_dir = args.model_dir.resolve()
    if not (model_dir / "model.py").is_file():
        print(f"model.py が無い: {model_dir / 'model.py'}", file=sys.stderr)
        return 1
    mod = load(model_dir)
    parts = getattr(mod, "parts", None) or {}

    coupons = getattr(mod, "coupons", None) or {}
    shapes = {"": mod.result, **{f"-{k}": v for k, v in parts.items()},
              **{f"-coupon-{k}": v for k, v in coupons.items()}}
    bad = [k or "result" for k, v in shapes.items() if not v.is_valid]
    if bad:
        print(f"ERROR: 不正なソリッド（is_valid = False）: {', '.join(bad)}", file=sys.stderr)
        return 1

    name = model_dir.name
    out = model_dir / "out"
    out.mkdir(exist_ok=True)
    for old in out.glob(f"{name}-*.st[el]*"):   # 消えたパーツの古いファイルを残さない
        if old.suffix in (".stl", ".step"):
            old.unlink()
    for suffix, shape in shapes.items():
        _export(shape, out / f"{name}{suffix}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
