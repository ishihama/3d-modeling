"""可動部のチェック（回転させたときの干渉、振り子の周期）。"""

from __future__ import annotations

import math

from build123d import Axis


def sweep_interference(moving, others: dict, origin, direction, angles) -> list[dict]:
    """moving を軸（origin, direction）まわりに angles（度）で回し、others の各形状との重なり体積（mm³）を返す。

    戻り値: [{"angle": 角度, "overlap": {名前: 体積}}]（計算に失敗した組は −1）
    """
    axis = Axis(origin, direction)
    table = []
    for ang in angles:
        moved = moving.rotate(axis, ang)
        overlap = {}
        for key, other in others.items():
            try:
                inter = other & moved
                overlap[key] = round(float(inter.volume) if inter else 0.0, 4)
            except Exception:
                overlap[key] = -1.0
        table.append({"angle": ang, "overlap": overlap})
    return table


def pendulum_period(shape, origin, direction, gravity: float = 9810.0) -> float:
    """軸まわりに自重で揺れる剛体（密度一様）の周期 T = 2π √(I / (m g d)) [秒]。gravity は mm/s²。"""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, props)
    inertia = props.MomentOfInertia(gp_Ax1(gp_Pnt(*origin), gp_Dir(*direction)))
    com = props.CentreOfMass()
    n = math.sqrt(sum(c * c for c in direction))
    u = [c / n for c in direction]
    r = [com.X() - origin[0], com.Y() - origin[1], com.Z() - origin[2]]
    along = sum(a * b for a, b in zip(r, u))
    d = math.sqrt(max(sum(c * c for c in r) - along * along, 0.0))   # 軸と重心の距離
    return 2 * math.pi * math.sqrt(inertia / (props.Mass() * gravity * d))
