"""STL の印刷可能性チェック（Bambu Lab X2D / CLAUDE.md の設計ルール準拠）。

使い方:
    uv run tools/check_stl.py <stl> [--toy] [--dual] [--allow-supports]

判定:
    - 水密・面の向き（法線の一貫性）・体積            … ERROR
    - 造形範囲（設計上限 = 各辺 -3 mm。--dual で 2 ノズル範囲） … ERROR
    - 45° 超の下向き面（ベッド接地面を除く）が表面積の 2% 超
                                                  … ERROR（--allow-supports で WARN）
    - ベッド接地面積 50 mm² 未満                     … WARN
    - 肉厚近似（表面サンプリング＋内向きレイ）で 1.2 mm 未満が 5% 超 … WARN
    - --toy: ボディ分割し、小部品シリンダー（内径 31.7・深さ 57.1 mm）に
      入りうるボディ（本体を含む）                    … ERROR

結果は STL と同じ場所に <name>.check.json として保存する。
終了コード: ERROR が 0 件なら 0、1 件以上なら 1。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import trimesh

# --- X2D 造形範囲（mm）と設計マージン ---------------------------------------
BUILD_VOLUME_SINGLE = (256.0, 256.0, 260.0)
BUILD_VOLUME_DUAL = (235.5, 256.0, 256.0)
DESIGN_MARGIN = 3.0

# --- 設計ルール -------------------------------------------------------------
OVERHANG_MAX_DEG = 45.0          # 垂直からの角度
OVERHANG_TOL_DEG = 1.0           # 45° 面取りを誤検出しないための許容
OVERHANG_AREA_RATIO = 0.02       # 表面積に対する割合
BED_TOL_Z = 0.01                 # ベッド接地面とみなす Z の許容
BED_CONTACT_MIN_AREA = 50.0      # mm²
MIN_WALL = 1.2                   # mm
THIN_RATIO = 0.05
THICKNESS_SAMPLES = 4000
RAY_EPS = 1e-3

# --- 小部品シリンダー（16 CFR 1501 / ASTM F963 相当） ------------------------
SMALL_PARTS_DIAMETER = 31.7
SMALL_PARTS_DEPTH = 57.1
SMALL_PARTS_DIRECTIONS = 3000


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add(self, level: str, code: str, message: str, **data) -> None:
        self.items.append({"level": level, "code": code, "message": message, **data})

    def count(self, level: str) -> int:
        return sum(1 for i in self.items if i["level"] == level)


# ---------------------------------------------------------------------------
# 個別チェック
# ---------------------------------------------------------------------------

def check_integrity(mesh: trimesh.Trimesh, rep: Report) -> bool:
    ok = True
    if mesh.is_watertight:
        rep.add("OK", "watertight", "水密")
    else:
        rep.add("ERROR", "watertight", "水密ではない（穴・非多様体エッジがある）")
        ok = False
    if mesh.is_winding_consistent:
        rep.add("OK", "winding", "面の向きが一貫している")
    else:
        rep.add("ERROR", "winding", "面の向き（法線）が一貫していない")
        ok = False
    vol = float(mesh.volume) if mesh.is_watertight else float("nan")
    if mesh.is_watertight and vol > 0:
        rep.add("OK", "volume", f"体積 {vol:.1f} mm³", value=round(vol, 3))
    elif not mesh.is_watertight:
        rep.add("ERROR", "volume", "水密でないため体積を計算できない")
        ok = False
    else:
        rep.add("ERROR", "volume", f"体積が正でない（{vol:.3f} mm³。法線が裏返っている可能性）",
                value=None if math.isnan(vol) else round(vol, 3))
        ok = False
    return ok


def check_build_volume(mesh: trimesh.Trimesh, dual: bool, rep: Report) -> None:
    vol = BUILD_VOLUME_DUAL if dual else BUILD_VOLUME_SINGLE
    lim = tuple(v - DESIGN_MARGIN for v in vol)
    x, y, z = (float(v) for v in mesh.extents)
    # XY はプレート上で 90° 回転できるので入れ替えも許す
    fits_xy = (x <= lim[0] and y <= lim[1]) or (y <= lim[0] and x <= lim[1])
    fits = fits_xy and z <= lim[2]
    mode = "2ノズル" if dual else "メイン"
    msg = (f"外形 {x:.1f} × {y:.1f} × {z:.1f} mm / 設計上限（{mode}）"
           f"{lim[0]:g} × {lim[1]:g} × {lim[2]:g} mm")
    rep.add("OK" if fits else "ERROR", "build_volume", msg,
            extents=[round(x, 3), round(y, 3), round(z, 3)], limit=list(lim))


def bed_face_mask(mesh: trimesh.Trimesh) -> np.ndarray:
    zmin = mesh.bounds[0][2]
    tri_z = mesh.triangles[:, :, 2]
    on_bed = np.all(tri_z <= zmin + BED_TOL_Z, axis=1)
    return on_bed & (mesh.face_normals[:, 2] < -0.99)


def check_overhang(mesh: trimesh.Trimesh, allow_supports: bool, rep: Report) -> None:
    bed = bed_face_mask(mesh)
    # 法線の Z 成分が -sin(45°) より下向き = 垂直から 45° を超えるオーバーハング
    limit = -math.sin(math.radians(OVERHANG_MAX_DEG + OVERHANG_TOL_DEG))
    over = (mesh.face_normals[:, 2] < limit) & ~bed
    area = float(mesh.area_faces[over].sum())
    ratio = area / float(mesh.area)
    msg = f"45° 超の下向き面 {area:.1f} mm²（表面積の {ratio * 100:.2f}%、上限 {OVERHANG_AREA_RATIO * 100:g}%）"
    if ratio <= OVERHANG_AREA_RATIO:
        level = "OK"
    else:
        level = "WARN" if allow_supports else "ERROR"
        msg += " → サポートが必要" + ("（--allow-supports により警告扱い）" if allow_supports else "")
    rep.add(level, "overhang", msg, area=round(area, 3), ratio=round(ratio, 5))


def check_bed_contact(mesh: trimesh.Trimesh, rep: Report) -> None:
    area = float(mesh.area_faces[bed_face_mask(mesh)].sum())
    level = "OK" if area >= BED_CONTACT_MIN_AREA else "WARN"
    rep.add(level, "bed_contact",
            f"ベッド接地面積 {area:.1f} mm²（推奨 {BED_CONTACT_MIN_AREA:g} mm² 以上）",
            area=round(area, 3))


def check_thickness(mesh: trimesh.Trimesh, rep: Report) -> None:
    points, face_idx = trimesh.sample.sample_surface(mesh, THICKNESS_SAMPLES, seed=0)
    normals = mesh.face_normals[face_idx]
    dirs = -normals
    origins = points + dirs * RAY_EPS
    locs, ray_idx, tri_idx = mesh.ray.intersects_location(origins, dirs, multiple_hits=False)
    # 内側から裏面（外向き法線がレイと同じ向き）に当たったものだけ採用
    exiting = np.einsum("ij,ij->i", mesh.face_normals[tri_idx], dirs[ray_idx]) > 0
    ray_idx, locs = ray_idx[exiting], locs[exiting]
    dist = np.linalg.norm(locs - origins[ray_idx], axis=1) + RAY_EPS
    if len(dist) == 0:
        rep.add("WARN", "thickness", "肉厚を測定できなかった（レイが当たらない）")
        return
    thin = dist < MIN_WALL - 1e-3
    ratio = float(thin.mean())
    p5 = float(np.percentile(dist, 5))
    level = "OK" if ratio <= THIN_RATIO else "WARN"
    rep.add(level, "thickness",
            f"肉厚 {MIN_WALL} mm 未満のサンプル {ratio * 100:.1f}%（上限 {THIN_RATIO * 100:g}%）、"
            f"最小 {dist.min():.2f} mm / 5パーセンタイル {p5:.2f} mm（{len(dist)} 点で測定）",
            ratio=round(ratio, 5), min=round(float(dist.min()), 3), p5=round(p5, 3),
            samples=int(len(dist)))


# --- 小部品シリンダー --------------------------------------------------------

def _min_enclosing_circle(pts: np.ndarray) -> float:
    """2D 点群の最小包含円の直径（Welzl の反復版）。"""
    rng = np.random.default_rng(0)
    p = pts[rng.permutation(len(pts))]

    def circle2(a, b):
        c = (a + b) / 2
        return c, np.linalg.norm(a - c)

    def circle3(a, b, c):
        ax, ay = a
        bx, by = b
        cx, cy = c
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-12:  # 共線 → 最も遠い 2 点
            cands = [circle2(a, b), circle2(a, c), circle2(b, c)]
            return max(cands, key=lambda t: t[1])
        ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
        uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
        ctr = np.array([ux, uy])
        return ctr, np.linalg.norm(a - ctr)

    eps = 1e-9
    c, r = p[0], 0.0
    for i in range(1, len(p)):
        if np.linalg.norm(p[i] - c) <= r + eps:
            continue
        c, r = p[i], 0.0
        for j in range(i):
            if np.linalg.norm(p[j] - c) <= r + eps:
                continue
            c, r = circle2(p[i], p[j])
            for k in range(j):
                if np.linalg.norm(p[k] - c) <= r + eps:
                    continue
                c, r = circle3(p[i], p[j], p[k])
    return 2 * r


def _fit_in_direction(verts: np.ndarray, d: np.ndarray) -> tuple[float, float]:
    """軸方向 d に入れたときの (長さ, 断面の最小包含円直径)。"""
    d = d / np.linalg.norm(d)
    length = float(np.ptp(verts @ d))
    a = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(d, a)
    u /= np.linalg.norm(u)
    v = np.cross(d, u)
    proj = np.column_stack([verts @ u, verts @ v])
    if len(proj) > 3:
        try:
            from scipy.spatial import ConvexHull
            proj = proj[ConvexHull(proj).vertices]
        except Exception:
            pass
    return length, _min_enclosing_circle(proj)


def _fibonacci_sphere(n: int) -> np.ndarray:
    # 軸の向きは ±d で同じなので半球で十分
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - i / n)
    theta = math.pi * (1 + 5**0.5) * i
    return np.column_stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)])


def small_parts_fit(body: trimesh.Trimesh) -> dict:
    """ボディが小部品シリンダーに入りうるかを方向サンプリングで判定する。"""
    verts = np.asarray(body.convex_hull.vertices)
    # 外接球の直径がシリンダー径以下なら確実に入る（長さも直径以下）
    if np.ptp(verts, axis=0).max() <= SMALL_PARTS_DIAMETER / math.sqrt(3):
        return {"fits": True, "length": float(np.ptp(verts, axis=0).max()),
                "diameter": float(np.linalg.norm(np.ptp(verts, axis=0)))}
    dirs = np.vstack([np.eye(3), body.principal_inertia_vectors, _fibonacci_sphere(SMALL_PARTS_DIRECTIONS)])
    best = None
    for d in dirs:
        length = float(np.ptp(verts @ (d / np.linalg.norm(d))))
        if length > SMALL_PARTS_DEPTH:
            continue
        length, dia = _fit_in_direction(verts, d)
        if best is None or dia < best[2]:
            best = (d, length, dia)
    if best is None:
        return {"fits": False, "length": None, "diameter": None}
    # 最良方向の周辺を局所探索して取りこぼしを減らす
    rng = np.random.default_rng(0)
    d0 = best[0] / np.linalg.norm(best[0])
    for scale in (0.05, 0.02, 0.005):
        for _ in range(60):
            d = d0 + rng.normal(scale=scale, size=3)
            length, dia = _fit_in_direction(verts, d)
            if length <= SMALL_PARTS_DEPTH and dia < best[2]:
                best = (d, length, dia)
                d0 = d / np.linalg.norm(d)
    _, length, dia = best
    return {"fits": bool(dia <= SMALL_PARTS_DIAMETER), "length": round(length, 3),
            "diameter": round(dia, 3), "axis": [round(float(x), 4) for x in d0]}


def check_small_parts(mesh: trimesh.Trimesh, rep: Report) -> None:
    bodies = mesh.split(only_watertight=False)
    bodies = sorted(bodies, key=lambda b: -abs(b.volume))
    results = []
    for i, body in enumerate(bodies):
        r = small_parts_fit(body)
        r["body"] = i
        r["extents"] = [round(float(v), 3) for v in body.extents]
        results.append(r)
        label = "本体" if i == 0 else f"パーツ {i}"
        ext = " × ".join(f"{v:.1f}" for v in body.extents)
        if r["fits"]:
            rep.add("ERROR", "small_parts",
                    f"{label}（{ext} mm）が小部品シリンダー（内径 {SMALL_PARTS_DIAMETER}・深さ "
                    f"{SMALL_PARTS_DEPTH} mm）に入る: 断面径 {r['diameter']:.1f} mm / 長さ {r['length']:.1f} mm",
                    **r)
        else:
            detail = ("どの向きでも深さ・径を超える" if r["diameter"] is None
                      else f"最小断面径 {r['diameter']:.1f} mm（長さ {r['length']:.1f} mm の向き）")
            rep.add("OK", "small_parts", f"{label}（{ext} mm）は小部品シリンダーに入らない: {detail}", **r)
    rep.add("INFO", "bodies", f"ボディ数 {len(bodies)}", value=len(bodies))


# ---------------------------------------------------------------------------

def run(path: Path, toy: bool, dual: bool, allow_supports: bool) -> Report:
    rep = Report()
    mesh = trimesh.load(path, force="mesh", process=True)
    mesh.merge_vertices()
    sound = check_integrity(mesh, rep)
    check_build_volume(mesh, dual, rep)
    check_overhang(mesh, allow_supports, rep)
    check_bed_contact(mesh, rep)
    if sound:
        check_thickness(mesh, rep)
    else:
        rep.add("WARN", "thickness", "メッシュが不正なため肉厚チェックを省略")
    if toy:
        check_small_parts(mesh, rep)
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STL の印刷可能性チェック（Bambu Lab X2D）")
    ap.add_argument("stl", type=Path)
    ap.add_argument("--toy", action="store_true", help="子供向け: 小部品シリンダー判定を行う")
    ap.add_argument("--dual", action="store_true", help="2 ノズル同時使用の造形範囲で判定する")
    ap.add_argument("--allow-supports", action="store_true", help="オーバーハング超過を警告扱いにする")
    args = ap.parse_args(argv)

    if not args.stl.is_file():
        print(f"ファイルが無い: {args.stl}", file=sys.stderr)
        return 1

    rep = run(args.stl, args.toy, args.dual, args.allow_supports)
    errors, warns = rep.count("ERROR"), rep.count("WARN")

    for item in rep.items:
        print(f"[{item['level']:5}] {item['code']:13} {item['message']}")
    print(f"\nERROR {errors} / WARN {warns} → {'NG' if errors else 'PASS'}")

    out = args.stl.with_suffix(".check.json")
    out.write_text(json.dumps({
        "file": str(args.stl),
        "options": {"toy": args.toy, "dual": args.dual, "allow_supports": args.allow_supports},
        "summary": {"errors": errors, "warnings": warns, "passed": errors == 0},
        "checks": rep.items,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"結果: {out}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
