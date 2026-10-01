"""desk-tray: 机上の小物トレイ（仕様は spec.md）。

書き出し: uv run tools/export_model.py models/desk-tray  → out/desk-tray.step, .stl
build123d-mcp: execute_file("models/desk-tray/model.py")（.mcp.json の PYTHONPATH で printlib を読める）
（MCP のサンドボックスは build123d 等以外の import を禁止するため、このファイルは形状定義だけにする）
"""

from build123d import Axis, Box, Pos

from printlib import rim_radius, rules, tapered_bin

# --- パラメータ（spec.md） ---------------------------------------------------
WIDTH = 100.0            # X
DEPTH = 60.0             # Y
HEIGHT = 40.0            # Z
WALL = 2.0               # 外壁・仕切りの厚さ
FLOOR = 1.6              # 底面厚
CORNER_R = 4.0           # 縦の角 R
COMPARTMENTS = 3         # 幅方向の区画数
DIVIDER_DROP = 5.0       # 仕切りは外壁より何 mm 低いか
BOTTOM_CHAMFER = rules.BOTTOM_CHAMFER   # 底面外周の面取り 0.5
TOP_FILLET = rules.TOP_EDGE_R           # 上端 R1（肉厚 2.0 の両縁なので rim_radius で R0.99 になる）


def build():
    inner_w = WIDTH - 2 * WALL
    inner_d = DEPTH - 2 * WALL
    pitch = (inner_w - (COMPARTMENTS - 1) * WALL) / COMPARTMENTS
    divider_x = [-inner_w / 2 + pitch * (i + 1) + WALL * i + WALL / 2 for i in range(COMPARTMENTS - 1)]
    divider_h = HEIGHT - DIVIDER_DROP - FLOOR

    # 1〜3. 外形（縦の角 R4）・底面外周の面取り・くり抜き（内側の角 R = 外側 R − 肉厚）
    box = tapered_bin((WIDTH, DEPTH), (WIDTH, DEPTH), HEIGHT, CORNER_R, wall=WALL, floor=FLOOR,
                      bottom_chamfer=BOTTOM_CHAMFER, rim_r=None)
    # 4. 仕切り
    tray = box
    for x in divider_x:
        tray = tray + Pos(x, 0, FLOOR + divider_h / 2) * Box(WALL, inner_d, divider_h)
    # 5. 上端 R1（外壁の縁 + 仕切りの上端）
    top_edges = tray.edges().filter_by_position(Axis.Z, HEIGHT, HEIGHT)
    divider_top = tray.edges().filter_by_position(Axis.Z, HEIGHT - DIVIDER_DROP, HEIGHT - DIVIDER_DROP)
    tray = tray.fillet(rim_radius(TOP_FILLET, WALL), top_edges + divider_top.filter_by(Axis.Y))

    return tray


# build123d-mcp の execute_file と tools/export_model.py はこの変数を読む
result = build()
