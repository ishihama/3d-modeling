"""容器などの基本形状。"""

from __future__ import annotations

from build123d import (
    Axis,
    BuildPart,
    BuildSketch,
    Circle,
    Locations,
    Plane,
    Polygon,
    RectangleRounded,
    Sketch,
    loft,
)

from . import rules


def rim_radius(r: float, wall: float) -> float:
    """肉厚 wall の縁に付ける R。両縁 R でちょうど半円になる場合は OCC が失敗するのでわずかに小さくする。"""
    return min(r, wall / 2 - rules.FILLET_EPS)


def _rrect_at(plane: Plane, w: float, d: float, r: float):
    """plane 上の角丸長方形（スケッチ）。ビルダーは関数をまたいで親に結び付かないため、オブジェクトで返す。"""
    return plane * RectangleRounded(w, d, r)


def _lerp(z: float, height: float, bottom: float, top: float) -> float:
    return bottom + (top - bottom) * z / height


def tapered_block(bottom: tuple[float, float], top: tuple[float, float], height: float,
                  corner_r: float, bottom_chamfer: float = rules.BOTTOM_CHAMFER):
    """底面 bottom=(W, D) から上面 top=(W, D) へ直線的に変わる角丸ブロック（縦の角 R = corner_r）。

    底面外周の面取りは断面に含めて直線ロフトで作る（ロフト後の曲面エッジへの chamfer は OCC で失敗するため）。
    bottom == top なら普通の角丸直方体になる。
    """
    c = bottom_chamfer
    sections = []
    if c > 0:
        sections.append(_rrect_at(Plane.XY, bottom[0] - 2 * c, bottom[1] - 2 * c, corner_r - c))
        sections.append(_rrect_at(Plane.XY.offset(c), _lerp(c, height, bottom[0], top[0]),
                                  _lerp(c, height, bottom[1], top[1]), corner_r))
    else:
        sections.append(_rrect_at(Plane.XY, bottom[0], bottom[1], corner_r))
    sections.append(_rrect_at(Plane.XY.offset(height), top[0], top[1], corner_r))
    with BuildPart() as p:
        loft(sections, ruled=True)
    return p.part


def tapered_bin(bottom: tuple[float, float], top: tuple[float, float], height: float, corner_r: float,
                wall: float = rules.STRUCT_WALL, floor: float = rules.MIN_FLOOR,
                bottom_chamfer: float = rules.BOTTOM_CHAMFER, rim_r: float | None = rules.TOP_EDGE_R):
    """上面が開いた容器。外形は tapered_block、内面は外形と平行（肉厚は水平方向で wall）。

    rim_r: 上端の縁の R（None なら付けない）。肉厚の半分以上は rim_radius() で自動的に丸める。
    """
    outer = tapered_block(bottom, top, height, corner_r, bottom_chamfer)
    z_top = height + 1.0   # 上端より上まで延長して貫通させる
    inner = [
        _rrect_at(Plane.XY.offset(z), _lerp(z, height, bottom[0], top[0]) - 2 * wall,
                  _lerp(z, height, bottom[1], top[1]) - 2 * wall, corner_r - wall)
        for z in (floor, z_top)
    ]
    with BuildPart() as cavity:
        loft(inner, ruled=True)
    part = outer - cavity.part
    if rim_r:
        part = part.fillet(rim_radius(rim_r, wall), part.edges().filter_by_position(Axis.Z, height, height))
    return part


def teardrop(plane: Plane, d: float, center: tuple[float, float] = (0.0, 0.0),
             flat_top: float | None = None) -> Sketch:
    """水平穴用の涙滴形スケッチ（plane の +Y 方向＝印刷時の上に尖る）。

    円に 45° の接線を足した形なので、サポート無しで造形できる。
    flat_top: 尖りを中心からこの高さで切る（例: d / 2 + 0.3）。None なら尖らせたまま。
    穴として使う（回転する軸には使わない: 先端が相手に当たる）。
    """
    r = d / 2
    t = r / 2 ** 0.5                     # 45° の接点
    apex = r * 2 ** 0.5
    cx, cy = center
    with BuildSketch(plane) as sk:
        with Locations(center):
            Circle(r)
        if flat_top is None or flat_top >= apex:
            Polygon((cx + t, cy + t), (cx, cy + apex), (cx - t, cy + t), (cx, cy), align=None)
        else:
            w = apex - flat_top          # 切った位置での半幅（45° なので高さの差と同じ）
            Polygon((cx + t, cy + t), (cx + w, cy + flat_top), (cx - w, cy + flat_top), (cx - t, cy + t), (cx, cy),
                    align=None)
    return sk.sketch
