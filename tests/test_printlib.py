"""printlib のテスト（pytest 不要）。

    uv run tests/test_printlib.py
"""

from __future__ import annotations

import math
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from build123d import Axis, Box, BuildPart, Locations, Mode, Plane, export_step, extrude  # noqa: E402

from printlib import (  # noqa: E402
    crop,
    mark_notches,
    SnapPivot,
    flip_for_print,
    make_assembly,
    on_bed,
    pendulum_period,
    rim_radius,
    rules,
    sweep_interference,
    tapered_bin,
    tapered_block,
    teardrop,
)


def approx(a, b, tol=1e-3):
    assert abs(a - b) <= tol, f"{a} != {b} (tol {tol})"


# --- rules / shapes -----------------------------------------------------------

def test_rim_radius():
    approx(rim_radius(1.0, 2.0), 1.0 - rules.FILLET_EPS)   # ちょうど半円 → わずかに小さく
    approx(rim_radius(1.0, 3.0), 1.0)                       # 余裕があればそのまま


def test_tapered_block_straight_is_rounded_box():
    b = tapered_block((100, 60), (100, 60), 40, 4, bottom_chamfer=0)
    rounded_area = 100 * 60 - (4 - math.pi) * 4 ** 2
    approx(b.volume, rounded_area * 40, 0.5)
    size = b.bounding_box().size
    approx(size.X, 100); approx(size.Y, 60); approx(size.Z, 40)


def test_tapered_block_bottom_chamfer():
    b = tapered_block((100, 60), (100, 60), 40, 4)
    bottom = b.faces().sort_by(Axis.Z)[0].bounding_box().size
    approx(bottom.X, 100 - 2 * rules.BOTTOM_CHAMFER, 1e-2)   # 底面は面取りの分だけ小さい


def test_tapered_bin_walls_and_floor():
    p = tapered_bin((134, 56), (150, 70), 160, 8, wall=2.0, floor=2.0)
    assert p.is_valid
    size = p.bounding_box().size
    approx(size.Z, 160)
    # 中央を縦に貫く線: 底 2.0 だけが材料
    from build123d import Edge
    hits = sorted({round(v.Z, 3) for v in (p & Edge.make_line((0, 0, -1), (0, 0, 200))).vertices()})
    assert hits == [0.0, 2.0], hits
    # 高さ 80 での X 方向の壁の厚さ（水平方向で 2.0）
    xs = sorted({round(v.X, 3) for v in (p & Edge.make_line((-100, 0, 80), (100, 0, 80))).vertices()})
    approx(xs[1] - xs[0], 2.0, 1e-2)
    approx(xs[-1] - xs[-2], 2.0, 1e-2)


def test_teardrop():
    d = 5.0
    t = teardrop(Plane.XY, d)
    bb = t.bounding_box()
    approx(bb.max.Y, d / 2 * math.sqrt(2), 1e-3)   # 45° の尖り
    approx(bb.min.Y, -d / 2, 1e-3)
    approx(bb.size.X, d, 1e-3)
    flat = teardrop(Plane.XY, d, flat_top=d / 2 + 0.3)
    approx(flat.bounding_box().max.Y, d / 2 + 0.3, 1e-3)


# --- pivot / motion -----------------------------------------------------------

def _pivot_pair(pv: SnapPivot, axis_z=20.0):
    """テスト用: 軸受けブロック（溝つき）と、軸つきの小さな板。"""
    with BuildPart() as block:
        with Locations((0, 0, axis_z - pv.depth + (pv.depth + 4) / 2)):
            Box(6, 14, pv.depth + 4)
        extrude(pv.seat_sketch(Plane.YZ, (0, axis_z)), amount=4, both=True, mode=Mode.SUBTRACT)
    with BuildPart() as pin:
        extrude(pv.pin_sketch(Plane.YZ.offset(-6), (0, axis_z)), amount=12)
    return block.part, pin.part


def test_pivot_auto_straight_centers_pin():
    pv = SnapPivot(pin_d=5.0)
    r, n = pv.pin_d / 2, pv.neck_half
    # 軸が入口の角に乗ったときの中心の高さ = 角の高さ + √(r² − n²) が、穴の中心（0）と一致する
    corner_z = -(pv.straight_len + pv.lip_drop)
    approx(corner_z + math.sqrt(r * r - n * n), 0.0, 1e-9)
    assert pv.neck_half * 2 < pv.pin_d          # 入口は軸より狭い（スナップ）
    assert pv.seat_r > pv.pin_d / 2             # 穴は軸より広い


def test_pivot_rotates_without_interference():
    pv = SnapPivot(pin_d=5.0)
    block, pin = _pivot_pair(pv)
    assert block.is_valid and pin.is_valid
    table = sweep_interference(pin, {"block": block}, (0, 0, 20.0), (1, 0, 0), range(-90, 91, 15))
    assert all(row["overlap"]["block"] == 0 for row in table), table


def test_sweep_interference_detects_collision():
    bar = Box(2, 30, 2)                                         # Y 方向に長い棒（原点中心）
    cube = Locations((0, 0, 10)).locations[0] * Box(10, 10, 10)  # 棒の真上（Z 5〜15）
    table = sweep_interference(bar, {"cube": cube}, (0, 0, 0), (1, 0, 0), [0, 90])
    assert table[0]["overlap"]["cube"] == 0                     # 水平なら当たらない
    assert table[1]["overlap"]["cube"] > 0, table               # 90° 起こすと突き刺さる


def test_pendulum_period_matches_analytic():
    a, b, c = 10.0, 40.0, 3.0
    plate = Box(a, b, c)
    # 板の端（Y = +b/2）で X 軸まわりに吊る: I = m (b² + c²)/12 + m (b/2)²、d = b/2
    inertia_per_m = (b * b + c * c) / 12 + (b / 2) ** 2
    expected = 2 * math.pi * math.sqrt(inertia_per_m / (9810.0 * b / 2))
    approx(pendulum_period(plate, (0, b / 2, 0), (1, 0, 0)), expected, 1e-6)


# --- assembly -----------------------------------------------------------------

def test_on_bed_and_flip():
    box = Locations((0, 0, 30)).locations[0] * Box(10, 20, 4)
    approx(on_bed(box).bounding_box().min.Z, 0.0)
    flipped = flip_for_print(box)
    approx(flipped.bounding_box().min.Z, 0.0)
    approx(flipped.bounding_box().size.Z, 4.0)


def test_make_assembly_keeps_parts_exportable():
    a, b = Box(10, 10, 10), Locations((20, 0, 0)).locations[0] * Box(5, 5, 5)
    asm = make_assembly("asm", {"a": a, "b": b})
    assert [c.label for c in asm.children] == ["a", "b"]
    with tempfile.TemporaryDirectory() as d:
        export_step(asm, f"{d}/asm.step")
        export_step(a, f"{d}/a.step")      # 子にしたのは複製なので、元の形状も単体で出力できる
        export_step(b, f"{d}/b.step")


# --- coupon -------------------------------------------------------------------

def test_crop():
    b = Box(20, 20, 20)
    piece = crop(b, (0, -5, -10), (10, 5, 10))
    approx(piece.volume, 10 * 10 * 20, 1e-6)
    approx(piece.bounding_box().min.X, 0.0)


def test_mark_notches():
    plate = Box(30, 20, 3)
    marked = mark_notches(plate, 3, (-5, 10, 0), (1, 0, 0), width=1.2, depth=1.2)
    approx(plate.volume - marked.volume, 3 * 1.2 * 1.2 * 3, 1e-6)   # 3 本 × 幅 × 奥行 × 板厚
    assert marked.is_valid


# ------------------------------------------------------------------------------

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
