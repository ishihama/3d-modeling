"""印刷の向き・組み立て状態の扱い。"""

from __future__ import annotations

from copy import copy

from build123d import Compound, Pos, Rot


def on_bed(shape):
    """最下点が Z = 0 に来るよう平行移動する。"""
    return Pos(0, 0, -shape.bounding_box().min.Z) * shape


def flip_for_print(shape):
    """X 軸まわりに 180° 回して（上下逆さ）ベッドに置く。蓋など、上面を下にして印刷するパーツ用。"""
    return on_bed(Rot(180, 0, 0) * shape)


def make_assembly(label: str, shapes: dict):
    """{名前: 形状} から組み立て状態の Compound を作る（子のラベル = 名前）。

    Compound の子になった形状は単体で STEP 出力できなくなるため、複製を子にする（元の形状は parts にそのまま使える）。
    """
    children = []
    for name, s in shapes.items():
        c = copy(s)
        c.label = name
        children.append(c)
    return Compound(children=children, label=label)
