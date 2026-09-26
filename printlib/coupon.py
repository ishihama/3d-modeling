"""試し刷りクーポン（はめあい・スナップなど、実物でしか決まらない寸法を小さく刷って確かめる）用の部品。

使い方（slim-bin が見本）:
    piece = crop(lid, (x0, y0, z0), (x1, y1, z1))           # 形状の一部を箱で切り出す
    piece = mark_notches(piece, 3, (x, y, z), (1, 0, 0))    # 見分け用の刻みを 3 本
model.py の `coupons = {"<名前>": 形状（印刷の向き）}` に入れると、export_model が
out/<name>-coupon-<名前>.stl として書き出し、e2e が他のパーツと同じくチェックする。
"""

from __future__ import annotations

from build123d import Box, Pos


def crop(shape, lo: tuple[float, float, float], hi: tuple[float, float, float]):
    """shape のうち、箱 lo〜hi（各軸の最小・最大）の中の部分。"""
    size = [h - l for l, h in zip(lo, hi)]
    center = [(l + h) / 2 for l, h in zip(lo, hi)]
    return shape & (Pos(*center) * Box(*size))


def mark_notches(shape, n: int, start: tuple[float, float, float], step_dir: tuple[float, float, float],
                 width: float = 1.2, depth: float = 1.2, height: float = 50.0, pitch: float = 2.5):
    """縁に n 本の刻み（幅 width・奥行 depth の縦溝）を入れて、試し刷りの種類を見分けられるようにする。

    start: 1 本目の刻みの中心（縁の上の点）、step_dir: 刻みを並べる向き（単位ベクトル。X か Y 方向）。
    刻みは縁から内側へ depth だけ、Z 方向に height の範囲で切る（板を貫通させる想定）。
    """
    out = shape
    for i in range(n):
        c = [s + d * pitch * i for s, d in zip(start, step_dir)]
        along_x = abs(step_dir[0]) > abs(step_dir[1])
        cutter = Box(width, depth * 2, height) if along_x else Box(depth * 2, width, height)
        out = out - Pos(*c) * cutter
    return out
