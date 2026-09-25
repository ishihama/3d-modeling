"""CLAUDE.md の設計ルールの数値（mm）。model.py ではここを参照し、同じ値を書き写さない。"""

# 肉厚・底
MIN_WALL = 1.2               # 最小肉厚
STRUCT_WALL = 2.0            # 構造部・荷重がかかる部分
MIN_FLOOR = 1.2              # 底面厚
MIN_FEATURE = 0.8            # 最小フィーチャ（ノズル径 × 2）

# エッジ
BOTTOM_CHAMFER = 0.5         # 底面外周の面取り（エレファントフット対策）
TOP_EDGE_R = 1.0             # 上向きエッジ R
FILLET_EPS = 0.01            # 肉厚のちょうど半分の R は OCC で失敗するため、この分だけ小さくする

# クリアランス（片側）
CLEAR_FIXED = 0.15           # 固定（圧入・はめ込み）
CLEAR_LID = 0.25             # 蓋
CLEAR_MOVE = 0.4             # 可動（回転・スライド）

# 穴・造形
VERTICAL_HOLE_EXTRA = 0.2    # 垂直穴は設計径 + 0.2
MAX_OVERHANG_DEG = 45.0      # 垂直からの角度
MAX_BRIDGE = 10.0

# 刻印・エンボス
EMBOSS_DEPTH = 0.6
EMBOSS_MIN_LINE = 0.8

# 子供向け
TOY_MIN_PROTRUSION_D = 5.0   # 細い突起の直径
TOY_EDGE_R = 1.0
TOY_TIP_R = 2.0
