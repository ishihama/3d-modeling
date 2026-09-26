"""check_stl.py の動作確認用。ダミー STL を生成してチェックし、期待どおりの判定かを確かめる。

使い方:
    uv run tools/selftest_check_stl.py [--keep DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import trimesh
from trimesh.creation import box, icosphere, cylinder

sys.path.insert(0, str(Path(__file__).parent))
import check_stl  # noqa: E402


def on_bed(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m.apply_translation([0, 0, -m.bounds[0][2]])
    return m


def tray() -> trimesh.Trimesh:
    """100×60×40、肉厚 2.0、底 1.6、幅方向 3 区画（仕切りは 5 mm 低い）。"""
    outer = box([100, 60, 40])
    inner = box([96, 56, 40])
    inner.apply_translation([0, 0, 1.6])
    body = outer.difference(inner, engine="manifold")
    parts = [body]
    for x in (-96 / 6, 96 / 6):
        wall = box([2.0, 56.2, 35 - 1.6 + 0.1])
        wall.apply_translation([x, 0, -20 + 1.6 + (35 - 1.6) / 2 - 0.05])
        parts.append(wall)
    return on_bed(trimesh.boolean.union(parts, engine="manifold"))


def toy_with_small_part() -> trimesh.Trimesh:
    """車っぽい本体 + 外れた車輪 1 個（直径 20 mm）。"""
    body = box([80, 40, 30])
    body.apply_translation([0, 0, 15])
    wheel = cylinder(radius=10, height=8)
    wheel.apply_translation([70, 0, 4])
    return trimesh.util.concatenate([body, wheel])


def toy_ok() -> trimesh.Trimesh:
    """小部品シリンダーに入らないおもちゃ（直径 40 mm の球 + 台座）。"""
    ball = icosphere(subdivisions=4, radius=20)
    ball.apply_translation([0, 0, 25])
    base = cylinder(radius=15, height=8)
    base.apply_translation([0, 0, 4])
    return on_bed(trimesh.boolean.union([ball, base], engine="manifold"))


def toy_tiny() -> trimesh.Trimesh:
    """本体そのものが小さい（直径 24 mm の球）→ 本体も小部品扱い。"""
    return on_bed(icosphere(subdivisions=4, radius=12))


def mushroom() -> trimesh.Trimesh:
    """細い軸の上に大きな傘 → 水平オーバーハング。"""
    stem = box([10, 10, 20])
    stem.apply_translation([0, 0, 10])
    cap = box([60, 60, 5])
    cap.apply_translation([0, 0, 22.5])
    return trimesh.boolean.union([stem, cap], engine="manifold")


def peg() -> trimesh.Trimesh:
    """大きな台から水平に 20 mm 突き出た棒。下向き面は全体の 1% 未満なので面積チェックは通るが、実際はサポートが要る。"""
    base = box([60, 60, 40])
    base.apply_translation([0, 0, 20])
    rod = box([20, 6, 6])
    rod.apply_translation([40, 0, 33])
    return trimesh.boolean.union([base, rod], engine="manifold")


def short_peg() -> trimesh.Trimesh:
    """3 mm だけ突き出た小さな出っ張り → 短い片持ち（WARN）。"""
    base = box([60, 60, 40])
    base.apply_translation([0, 0, 20])
    nub = box([3, 6, 4])
    nub.apply_translation([31.5, 0, 30])
    return trimesh.boolean.union([base, nub], engine="manifold")


def tiny_nub() -> trimesh.Trimesh:
    """8 mm 角の小さな部品に 4 mm の出っ張り。下向き面は 16 mm² だが表面積の 3% 超（割合だけなら ERROR になっていた）。"""
    base = box([8, 8, 8])
    base.apply_translation([0, 0, 4])
    nub = box([4, 4, 3])
    nub.apply_translation([6, 0, 5.5])
    return trimesh.boolean.union([base, nub], engine="manifold")


def floating() -> trimesh.Trimesh:
    """台の上に、つながっていない板が浮いている。"""
    base = box([40, 40, 10])
    base.apply_translation([0, 0, 5])
    plate = box([20, 20, 3])
    plate.apply_translation([0, 0, 20])
    return trimesh.util.concatenate([base, plate])


def bridge(gap: float):
    def make() -> trimesh.Trimesh:
        """2 本の柱に板を渡す（柱の間隔 = ブリッジの長さ）。"""
        parts = []
        for sx in (-1, 1):
            pillar = box([10, 10, 20])
            pillar.apply_translation([sx * (gap / 2 + 5), 0, 10])
            parts.append(pillar)
        slab = box([gap + 20, 10, 3])
        slab.apply_translation([0, 0, 21.5])
        parts.append(slab)
        return trimesh.boolean.union(parts, engine="manifold")
    make.__doc__ = f"柱の間隔 {gap} mm のブリッジ"
    return make


def thin_box() -> trimesh.Trimesh:
    """肉厚 0.8 mm の箱。"""
    outer = box([50, 50, 30])
    inner = box([48.4, 48.4, 30])
    inner.apply_translation([0, 0, 0.8])
    return on_bed(outer.difference(inner, engine="manifold"))


def too_big() -> trimesh.Trimesh:
    # 単一 253 には入るが 2 ノズル 232.5 には入らない
    return on_bed(box([240, 240, 20]))


def open_mesh() -> trimesh.Trimesh:
    m = box([30, 30, 30])
    m.update_faces(list(range(len(m.faces) - 2)))  # 2 面削除して穴をあける
    return on_bed(m)


# (名前, 生成関数, オプション, 期待: {code: level})
CASES = [
    ("tray", tray, [], {"watertight": "OK", "overhang": "OK", "thickness": "OK", "bed_contact": "OK",
                        "layer_islands": "OK", "layer_bridges": "OK", "layer_cantilever": "OK"}),
    ("toy_small_part", toy_with_small_part, ["--toy"], {"small_parts": "ERROR"}),
    ("toy_tiny", toy_tiny, ["--toy"], {"small_parts": "ERROR"}),
    ("toy_ok", toy_ok, ["--toy"], {"small_parts": "OK", "overhang": "ERROR"}),
    ("mushroom", mushroom, [], {"overhang": "ERROR", "layer_cantilever": "ERROR"}),
    ("mushroom_supports", mushroom, ["--allow-supports"], {"overhang": "WARN", "layer_cantilever": "WARN"}),
    # 層ごとの解析でしか見つからないもの
    ("peg", peg, [], {"overhang": "OK", "layer_cantilever": "ERROR"}),
    ("short_peg", short_peg, [], {"layer_cantilever": "WARN"}),
    ("floating", floating, [], {"layer_islands": "ERROR"}),
    ("tiny_nub", tiny_nub, [], {"overhang": "OK", "layer_cantilever": "WARN"}),
    # 10 mm 以内のブリッジは面積チェックから除外され OK、超えると ERROR
    ("bridge_8mm", bridge(8.0), [], {"overhang": "OK", "layer_bridges": "OK", "layer_cantilever": "OK"}),
    ("bridge_30mm", bridge(30.0), [], {"layer_bridges": "ERROR"}),
    ("thin_box", thin_box, [], {"thickness": "WARN"}),
    ("too_big_single", too_big, [], {"build_volume": "OK"}),
    ("too_big_dual", too_big, ["--dual"], {"build_volume": "ERROR"}),
    ("open_mesh", open_mesh, [], {"watertight": "ERROR"}),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=Path, help="生成した STL / check.json を残すディレクトリ")
    args = ap.parse_args()
    outdir = args.keep or Path(tempfile.mkdtemp(prefix="check_stl_selftest_"))
    outdir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for name, make, opts, expect in CASES:
        stl = outdir / f"{name}.stl"
        make().export(stl)
        print(f"\n===== {name} {' '.join(opts)} =====")
        code = check_stl.main([str(stl), *opts])
        result = json.loads(stl.with_suffix(".check.json").read_text(encoding="utf-8"))
        levels: dict[str, set[str]] = {}
        for item in result["checks"]:
            levels.setdefault(item["code"], set()).add(item["level"])
        for key, want in expect.items():
            got = levels.get(key, set())
            # ERROR を期待する項目は「1 つでも ERROR」、OK を期待する項目は「すべて OK」
            ok = want in got if want != "OK" else got == {"OK"}
            if not ok:
                failures += 1
                print(f"  !! {key}: 期待 {want} / 実際 {sorted(got)}")
        if (code == 1) != (result["summary"]["errors"] > 0):
            failures += 1
            print(f"  !! 終了コード {code} が ERROR 数と不整合")

    print(f"\nselftest: {'PASS' if failures == 0 else f'FAIL ({failures})'}  出力: {outdir}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
