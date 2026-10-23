"""STL を 4 方向（正面・側面・上面・アイソメ）の PNG に描く（matplotlib。MCP / VTK 不要）。

使い方:
    uv run --group worker tools/render_views.py <stl> [--out-prefix PATH]

既定の出力: <stl と同じ場所>/<name>-{front,side,top,iso}.png
build123d-mcp の render_view が使えない環境（Cloud Run の 3D ワーカー、e2e --no-mcp）用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import trimesh

VIEWS = {            # (elevation, azimuth) … Z 上向き。正面は −Y 側から見る
    "front": (0, -90),
    "side": (0, 0),
    "top": (90, -90),
    "iso": (30, -60),
}
BASE_COLOR = np.array([0.45, 0.72, 0.85])
LIGHT = np.array([0.4, -0.6, 0.9])
SIZE_PX = 800
SUBDIVIDE_DIVS = 40      # 最長辺がモデルの大きさ / この値 を超える三角形を分割する（重ね順の誤りを減らす）
DPI = 100


def _shade(normals: np.ndarray, view_dir: np.ndarray) -> np.ndarray:
    light = LIGHT / np.linalg.norm(LIGHT)
    head = np.abs(normals @ view_dir)                 # 視線方向からの照明（裏面も明るく）
    key = np.clip(normals @ light, 0, 1)
    k = 0.28 + 0.45 * head + 0.35 * key
    return np.clip(BASE_COLOR[None, :] * k[:, None], 0, 1)


def render(stl: Path, out_prefix: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    mesh = trimesh.load(stl, force="mesh", process=True)
    lo, hi = mesh.bounds
    # 細長い大きな三角形は重心の奥行きで並べると前後を取り違えるので、細かく分けておく
    verts, faces = trimesh.remesh.subdivide_to_size(mesh.vertices, mesh.faces,
                                                    max_edge=float(np.max(hi - lo)) / SUBDIVIDE_DIVS)
    tris = verts[faces]
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    center, ext = (lo + hi) / 2, np.maximum(hi - lo, 1e-6)
    half = ext.max() / 2 * 1.05

    written = []
    for name, (elev, azim) in VIEWS.items():
        e, a = np.radians(elev), np.radians(azim)
        view_dir = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])
        # 裏を向いた面は描かない（matplotlib は面を奥から順に重ねるだけなので、裏面が手前に透けるのを防ぐ）
        front = normals @ view_dir > 1e-6
        colors = _shade(normals[front], view_dir)
        fig = plt.figure(figsize=(SIZE_PX / DPI, SIZE_PX / DPI), dpi=DPI)
        ax = fig.add_subplot(projection="3d", proj_type="ortho")
        # 面と同じ色の細い縁で、三角形のつなぎ目に出る隙間の線を埋める
        ax.add_collection3d(Poly3DCollection(tris[front], facecolors=colors, edgecolors=colors,
                                             linewidths=0.3, antialiased=False))
        for setter, c in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), center):
            setter(c - half, c + half)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        fig.subplots_adjust(0, 0, 1, 1)
        path = out_prefix.parent / f"{out_prefix.name}-{name}.png"
        fig.savefig(path, facecolor="white")
        plt.close(fig)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STL を 4 方向の PNG に描く")
    ap.add_argument("stl", type=Path)
    ap.add_argument("--out-prefix", type=Path, help="出力の接頭辞（既定: STL と同じ場所・同じ名前）")
    args = ap.parse_args(argv)
    if not args.stl.is_file():
        print(f"ファイルが無い: {args.stl}", file=sys.stderr)
        return 1
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("SKIP: matplotlib が無い（uv run --group worker で実行する）", file=sys.stderr)
        return 2
    prefix = args.out_prefix or args.stl.with_name(args.stl.stem)
    for p in render(args.stl, prefix):
        print(f"render: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
