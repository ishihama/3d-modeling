"""スナップ式の回転軸（丸軸 + 下から押し込む軸受けの溝）。

使い方（slim-bin のフラップが見本）:
    pv = SnapPivot(pin_d=5.0)
    extrude(pv.seat_sketch(plane, (0, axis_z)), ..., mode=Mode.SUBTRACT)   # 軸受け側（溝は plane の −Y 方向に開く）
    extrude(pv.pin_sketch(plane, (0, axis_z)), amount=pin_len)             # 軸側

溝の形（plane の +Y を上として）:
    穴径の半円 → 直線部 → 45° の返し → 狭い入口（軸径 − snap）→ 45° の呼び込み
- 溝の開口を上に向けて（= 軸受け側パーツを逆さにして）印刷すると、返しが 45° のオーバーハングになりサポート不要
- 軸は丸軸にする。涙滴形の軸は先端が中心から r√2 まで出るため、回転すると入口の角に必ず当たる
  （その代わり、横向きに印刷した軸の下側に小さなオーバーハングが残る。直径 5 × 長さ 4 程度なら造形できる）
- 動かしたときの干渉は printlib.sweep_interference で確かめる
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from build123d import BuildLine, BuildSketch, Circle, Locations, Plane, Polyline, Sketch, ThreePointArc, make_face

from . import rules


@dataclass(frozen=True)
class SnapPivot:
    pin_d: float = 5.0                    # 軸径
    clear: float = rules.CLEAR_MOVE       # 軸と穴の隙間（片側）
    snap: float = 0.4                     # 入口を軸径よりどれだけ狭くするか（PETG で 0.4 前後）
    neck: float = 1.0                     # 狭い入口の長さ
    mouth: float = 1.0                    # 45° の呼び込みの深さ
    straight: float | None = None         # 直線部の長さ。None なら「軸が返しに乗ったとき穴の中心に来る」長さを計算

    @property
    def seat_r(self) -> float:
        return self.pin_d / 2 + self.clear

    @property
    def neck_half(self) -> float:
        return (self.pin_d - self.snap) / 2

    @property
    def lip_drop(self) -> float:
        return self.seat_r - self.neck_half

    @property
    def straight_len(self) -> float:
        if self.straight is not None:
            return self.straight
        # 軸（半径 r）が入口の角（半幅 neck_half）に乗るとき、軸の中心は角より √(r² − n²) 上にある
        r, n = self.pin_d / 2, self.neck_half
        return max(0.0, math.sqrt(max(r * r - n * n, 0.0)) - self.lip_drop)

    @property
    def depth(self) -> float:
        """軸の中心から溝の開口端（呼び込みの端）までの距離。軸受けブロックの下端はここ。"""
        return self.straight_len + self.lip_drop + self.neck + self.mouth

    def seat_sketch(self, plane: Plane, center: tuple[float, float], extend: float = 1.0) -> Sketch:
        """軸受けの溝（plane 上、center が軸の中心、−Y 方向に開く）。extend だけ開口端の外まで伸ばして確実に貫通させる。"""
        u, a = center
        R, n = self.seat_r, self.neck_half
        b = a - self.straight_len
        neck_bottom = b - self.lip_drop - self.neck
        end = a - self.depth - extend
        spread = n + (neck_bottom - end)          # 45° の呼び込みを end まで延長したときの半幅
        with BuildSketch(plane) as sk:
            with BuildLine():
                ThreePointArc((u - R, a), (u, a + R), (u + R, a))
                Polyline(
                    (u + R, a), (u + R, b), (u + n, b - self.lip_drop), (u + n, neck_bottom),
                    (u + spread, end), (u - spread, end),
                    (u - n, neck_bottom), (u - n, b - self.lip_drop), (u - R, b), (u - R, a),
                )
            make_face()
        return sk.sketch

    def pin_sketch(self, plane: Plane, center: tuple[float, float]) -> Sketch:
        """軸の断面（丸）。"""
        with BuildSketch(plane) as sk:
            with Locations(center):
                Circle(self.pin_d / 2)
        return sk.sketch
