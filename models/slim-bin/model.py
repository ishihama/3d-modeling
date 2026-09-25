"""slim-bin: 洗面所・トイレ用の薄型ゴミ箱（スイング蓋）。仕様は spec.md。

3 パーツ: 本体 body / 蓋フレーム lid / スイングフラップ flap
- parts   … 各パーツを「印刷の向き」で並べた dict（tools/export_model.py が個別に STL 出力）
- result  … 組み立てた状態（使用時の向き）。MCP の execute_file とビューアはこれを使う
- motions … 可動部の定義（ビューアの角度スライダー・揺れの再生・干渉表示）

書き出し: uv run tools/export_model.py models/slim-bin
build123d-mcp: execute_file("models/slim-bin/model.py")（.mcp.json の PYTHONPATH で printlib を読める）
（MCP のサンドボックスは build123d 等以外の import を禁止するため、このファイルは形状定義だけにする）
"""

from build123d import (
    Axis,
    Box,
    BuildPart,
    BuildSketch,
    Circle,
    Locations,
    Mode,
    Plane,
    Rectangle,
    RectangleRounded,
    chamfer,
    extrude,
    fillet,
)

from printlib import SnapPivot, flip_for_print, make_assembly, on_bed, rules, tapered_bin

# --- 本体 -----------------------------------------------------------------
TOP_W = 150.0            # 上端外形 幅（X）
TOP_D = 70.0             # 上端外形 奥行（Y）
BOT_W = 134.0            # 底面外形 幅
BOT_D = 56.0             # 底面外形 奥行
HEIGHT = 160.0           # 本体高さ
WALL = 2.0               # 肉厚（水平方向で測る。傾き 3° 未満なので法線方向でもほぼ同じ）
FLOOR = 2.0              # 底面厚
CORNER_R = 8.0           # 縦の角 R（外側）
BOTTOM_CHAMFER = rules.BOTTOM_CHAMFER   # 底面外周の面取り 0.5
TOP_FILLET = rules.TOP_EDGE_R           # 上端 R1（肉厚 2.0 の両縁なので R0.99 になる）

# --- 蓋フレーム --------------------------------------------------------------
LID_FIT = rules.CLEAR_LID   # 本体上端とスカートの隙間（片側、蓋 0.25）
SKIRT_DEPTH = 10.0       # スカート深さ（天板下面から）
SKIRT_WALL = 2.0         # スカート肉厚
PLATE_T = 2.5            # 天板厚
OPEN_W = 118.0           # 投入口 幅
OPEN_D = 54.0            # 投入口 奥行（フラップ回転軌跡 + 1 mm）
OPEN_R = 4.0             # 投入口の角 R
LID_TOP_CHAMFER = 1.0    # 天板上面の外周・投入口の縁（印刷時はベッド側なので R ではなく 45° 面取り）

# --- フラップと軸 --------------------------------------------------------------
FLAP_W = 116.0           # フラップ 幅
FLAP_D = 50.0            # フラップ 奥行
FLAP_T = 3.0             # フラップ厚
FLAP_R = 3.0             # フラップの角 R
FLAP_TOP_FILLET = 1.0    # フラップ上面の縁 R
PIVOT_ABOVE_COM = 6.0    # 軸はフラップ板の中心より 6 mm 上（自重で水平に戻る）
PIN_D = 5.0              # 軸径
PIN_L = 4.0              # 軸の長さ（片側）
MOVE_CLEAR = rules.CLEAR_MOVE   # 可動クリアランス（片側 0.4）→ 軸受け穴 5.8
SNAP = 0.4               # 軸受けの入口を軸径よりどれだけ狭くするか
EAR_T = 3.0              # 軸を支える耳の厚さ（X）
EAR_HALF = 3.0           # 耳の半幅（Y）。上端は軸と同心の R3
BLOCK_GAP = 1.0          # 耳と軸受けブロックの隙間（X）
BLOCK_W = 6.0            # 軸受けブロック 幅（X）
BLOCK_D = 14.0           # 軸受けブロック 奥行（Y）
SEAT_TOP_MARGIN = 1.1    # 軸受け穴の上端から天板下面まで
SEAT_STRAIGHT = 0.4      # 軸受け穴の直線部（軸が入口の返しに乗ったとき、ほぼ穴の中心に来る長さ。計算値は 0.38）
NECK_L = 1.0             # スナップ入口（狭い部分）の長さ
MOUTH = 1.0              # 入口の 45° 呼び込みの深さ

# --- 導出値 -------------------------------------------------------------------
PIVOT = SnapPivot(pin_d=PIN_D, clear=MOVE_CLEAR, snap=SNAP, neck=NECK_L, mouth=MOUTH, straight=SEAT_STRAIGHT)
AXIS_Z = HEIGHT - SEAT_TOP_MARGIN - PIVOT.seat_r   # 156.0（使用時の軸の高さ）
FLAP_Z = AXIS_Z - PIVOT_ABOVE_COM                  # フラップ板中心
EAR_X0 = FLAP_W / 2 - EAR_T                        # 耳の内側 X
PIN_X0 = FLAP_W / 2                                # 軸の付け根 X
BLOCK_X0 = PIN_X0 + BLOCK_GAP                      # 軸受けブロックの内側 X
BLOCK_BOTTOM = AXIS_Z - PIVOT.depth                # 軸受けブロック下端


def build_body():
    # 外形（上に向かって広がる台形・底面外周の面取り込み）→ くり抜き（外形と平行な内面）→ 上端 R1
    return tapered_bin((BOT_W, BOT_D), (TOP_W, TOP_D), HEIGHT, CORNER_R, wall=WALL, floor=FLOOR,
                       bottom_chamfer=BOTTOM_CHAMFER, rim_r=TOP_FILLET)


def build_lid():
    skirt_in_w, skirt_in_d = TOP_W + 2 * LID_FIT, TOP_D + 2 * LID_FIT
    skirt_in_r = CORNER_R + LID_FIT
    out_w, out_d, out_r = skirt_in_w + 2 * SKIRT_WALL, skirt_in_d + 2 * SKIRT_WALL, skirt_in_r + SKIRT_WALL
    with BuildPart() as lid:
        # 1. 天板 + スカート
        with BuildSketch(Plane.XY.offset(HEIGHT - SKIRT_DEPTH)):
            RectangleRounded(out_w, out_d, out_r)
        extrude(amount=SKIRT_DEPTH + PLATE_T)
        with BuildSketch(Plane.XY.offset(HEIGHT - SKIRT_DEPTH)):
            RectangleRounded(skirt_in_w, skirt_in_d, skirt_in_r)
        extrude(amount=SKIRT_DEPTH, mode=Mode.SUBTRACT)
        # 2. 投入口
        with BuildSketch(Plane.XY.offset(HEIGHT)):
            RectangleRounded(OPEN_W, OPEN_D, OPEN_R)
        extrude(amount=PLATE_T, mode=Mode.SUBTRACT)
        # 3. 天板上面の縁を面取り（外周・投入口）
        chamfer(lid.edges().filter_by_position(Axis.Z, HEIGHT + PLATE_T, HEIGHT + PLATE_T), LID_TOP_CHAMFER)
        # 4. 軸受けブロック（天板の裏から下がる）
        block_h = HEIGHT - BLOCK_BOTTOM
        with Locations(*[(s * (BLOCK_X0 + BLOCK_W / 2), 0, BLOCK_BOTTOM + block_h / 2) for s in (-1, 1)]):
            Box(BLOCK_W, BLOCK_D, block_h)
        # 5. 軸受けの溝（X 方向に貫通。下に開くスナップ溝。蓋は逆さに印刷するので返しは 45° で造形できる）
        for s in (-1, 1):
            plane = Plane.YZ.offset(s * (BLOCK_X0 + BLOCK_W / 2))
            extrude(PIVOT.seat_sketch(plane, (0, AXIS_Z)), amount=BLOCK_W / 2 + 0.5, both=True, mode=Mode.SUBTRACT)
    return lid.part


def build_flap():
    with BuildPart() as flap:
        # 1. フラップ板
        with BuildSketch(Plane.XY.offset(FLAP_Z - FLAP_T / 2)):
            RectangleRounded(FLAP_W, FLAP_D, FLAP_R)
        extrude(amount=FLAP_T)
        # 2. 縁の処理（上面 R1、下面 0.5 面取り）
        fillet(flap.edges().filter_by_position(Axis.Z, FLAP_Z + FLAP_T / 2, FLAP_Z + FLAP_T / 2), FLAP_TOP_FILLET)
        chamfer(flap.edges().filter_by_position(Axis.Z, FLAP_Z - FLAP_T / 2, FLAP_Z - FLAP_T / 2), BOTTOM_CHAMFER)
        # 3. 耳（板の両端から軸の高さまで。上端は軸と同心の R）と軸（丸軸。理由は printlib.pivot を参照）
        ear_bottom = FLAP_Z + FLAP_T / 2 - 0.5   # 板に少し食い込ませて結合
        for s in (-1, 1):
            with BuildSketch(Plane.YZ.offset(s * (EAR_X0 + EAR_T / 2))):
                with Locations((0, (ear_bottom + AXIS_Z) / 2)):
                    Rectangle(2 * EAR_HALF, AXIS_Z - ear_bottom)
                with Locations((0, AXIS_Z)):
                    Circle(EAR_HALF)
            extrude(amount=EAR_T / 2, both=True)
            pin_plane = Plane.YZ.offset(s * PIN_X0)
            if s < 0:
                pin_plane = Plane(pin_plane.origin, x_dir=(0, -1, 0), z_dir=(-1, 0, 0))  # −X 向きに押し出す（上は +Z のまま）
            extrude(PIVOT.pin_sketch(pin_plane, (0, AXIS_Z)), amount=PIN_L)
    return flap.part


def build():
    body, lid, flap = build_body(), build_lid(), build_flap()
    body.label, lid.label, flap.label = "body", "lid", "flap"
    return body, lid, flap


_body, _lid, _flap = build()

# 印刷の向き: 本体は底を下、蓋フレームは天板を下（逆さ）、フラップは板を下
parts = {
    "body": _body,
    "lid": flip_for_print(_lid),
    "flap": on_bed(_flap),
}

# 組み立て状態（使用時の向き）
result = make_assembly("slim-bin", {"body": _body, "lid": _lid, "flap": _flap})

# 可動部の定義（tools/viewer.py が読み、角度スライダー・揺れの再生・干渉チェックに使う）
# origin / direction は使用時の座標。range は度。pendulum は自重で戻る振り子として揺れを再生する
motions = {
    "flap": {
        "label": "フラップ",
        "origin": (0.0, 0.0, AXIS_Z),
        "direction": (1.0, 0.0, 0.0),
        "range": (-90.0, 90.0),
        "pendulum": True,
    },
}
