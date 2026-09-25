"""desk-tray: 机上の小物トレイ（仕様は spec.md）。

書き出し: uv run tools/export_model.py models/desk-tray  → out/desk-tray.step, .stl
build123d-mcp: execute_file("models/desk-tray/model.py", result_name="desk-tray")
（MCP のサンドボックスは build123d 等以外の import を禁止するため、このファイルは形状定義だけにする）
"""

from build123d import (
    Axis,
    Box,
    BuildPart,
    BuildSketch,
    Locations,
    Mode,
    Plane,
    RectangleRounded,
    chamfer,
    extrude,
    fillet,
)

# --- パラメータ（spec.md） ---------------------------------------------------
WIDTH = 100.0            # X
DEPTH = 60.0             # Y
HEIGHT = 40.0            # Z
WALL = 2.0               # 外壁・仕切りの厚さ
FLOOR = 1.6              # 底面厚
CORNER_R = 4.0           # 縦の角 R
COMPARTMENTS = 3         # 幅方向の区画数
DIVIDER_DROP = 5.0       # 仕切りは外壁より何 mm 低いか
BOTTOM_CHAMFER = 0.5     # 底面外周の面取り
TOP_FILLET = 1.0         # 上端 R
# 肉厚 2.0 の両縁に R1 を付けると断面が完全な半円になり OCC のフィレットが失敗するため、
# わずかに小さくする（0.01 mm は印刷解像度以下。spec.md 変更履歴に記載）
FILLET_EPS = 0.01



def build():
    inner_w = WIDTH - 2 * WALL
    inner_d = DEPTH - 2 * WALL
    pitch = (inner_w - (COMPARTMENTS - 1) * WALL) / COMPARTMENTS
    divider_x = [-inner_w / 2 + pitch * (i + 1) + WALL * i + WALL / 2 for i in range(COMPARTMENTS - 1)]
    divider_h = HEIGHT - DIVIDER_DROP - FLOOR

    with BuildPart() as tray:
        # 1. 外形（縦の角 R4）
        with BuildSketch():
            RectangleRounded(WIDTH, DEPTH, CORNER_R)
        extrude(amount=HEIGHT)
        # 2. 底面外周の面取り
        chamfer(tray.faces().sort_by(Axis.Z)[0].edges(), BOTTOM_CHAMFER)
        # 3. くり抜き（内側の角 R = 外側 R − 肉厚）
        with BuildSketch(Plane.XY.offset(FLOOR)):
            RectangleRounded(inner_w, inner_d, CORNER_R - WALL)
        extrude(amount=HEIGHT, mode=Mode.SUBTRACT)
        # 4. 仕切り
        with Locations(*[(x, 0, FLOOR + divider_h / 2) for x in divider_x]):
            Box(WALL, inner_d, divider_h)
        # 5. 上端 R1（外壁の縁 + 仕切りの上端）
        top_edges = tray.edges().filter_by_position(Axis.Z, HEIGHT, HEIGHT)
        divider_top = tray.edges().filter_by_position(Axis.Z, HEIGHT - DIVIDER_DROP, HEIGHT - DIVIDER_DROP)
        fillet(top_edges + divider_top.filter_by(Axis.Y), TOP_FILLET - FILLET_EPS)

    return tray.part


# build123d-mcp の execute_file と tools/export_model.py はこの変数を読む
result = build()
