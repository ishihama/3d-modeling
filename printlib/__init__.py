"""printlib: モデル間で使い回す部品・機構・チェック（Bambu Lab X2D / CLAUDE.md の設計ルール準拠）。

model.py から import できる（build123d-mcp のサンドボックスでも読めるよう、
build123d / math / copy / dataclasses 等の許可されたモジュールだけを使う）。

    from printlib import rules, tapered_block, tapered_bin, SnapPivot, on_bed, flip_for_print, make_assembly
"""

from . import rules
from .assembly import flip_for_print, make_assembly, on_bed
from .motion import pendulum_period, sweep_interference
from .pivot import SnapPivot
from .shapes import rim_radius, tapered_bin, tapered_block, teardrop

__all__ = [
    "rules",
    "tapered_block", "tapered_bin", "rim_radius", "teardrop",
    "SnapPivot",
    "on_bed", "flip_for_print", "make_assembly",
    "sweep_interference", "pendulum_period",
]
