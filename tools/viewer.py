"""STL から単体で動く 3D ビューア HTML を生成する（外部ライブラリ・CDN 不使用、オフライン可）。

使い方:
    uv run tools/viewer.py <stl> [-o out.html]

- 既定の出力先は STL と同じ場所の <name>-viewer.html
- 同じ場所に <name>.check.json があれば、チェック結果も表示する
- ドラッグで回転、ホイール／ピンチで拡大縮小、ボタンで正面・側面・上面・アイソメ
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import trimesh

TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#f4f5f7; --panel:#ffffffe6; --text:#1d2330; --muted:#5b6475; --line:#d8dce3;
          --ok:#1f7a4d; --warn:#9a6700; --err:#b42318; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --panel:#23262de6; --text:#e8eaf0; --muted:#a2a9b8; --line:#353a44;
            --ok:#4cc38a; --warn:#e0b23b; --err:#f07167; }
  }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
               font:14px/1.5 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Segoe UI", sans-serif; }
  canvas { position:fixed; inset:0; width:100%; height:100%; touch-action:none; display:block; }
  .panel { position:fixed; left:12px; top:12px; max-width:min(360px, calc(100% - 24px));
           background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:10px 12px;
           backdrop-filter:blur(6px); }
  h1 { font-size:15px; margin:0 0 2px; }
  .dims { color:var(--muted); font-variant-numeric:tabular-nums; }
  .badge { display:inline-block; font-weight:600; padding:0 8px; border-radius:999px; margin-top:6px;
           border:1px solid currentColor; }
  .ok { color:var(--ok); } .warn { color:var(--warn); } .err { color:var(--err); }
  details { margin-top:6px; } summary { cursor:pointer; color:var(--muted); }
  ul { margin:4px 0 0; padding-left:0; list-style:none; font-size:12px; }
  li { margin:2px 0; } li b { display:inline-block; width:44px; }
  .views { position:fixed; left:50%; bottom:max(12px, env(safe-area-inset-bottom)); transform:translateX(-50%);
           display:flex; gap:6px; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:6px; }
  button { font:inherit; color:var(--text); background:transparent; border:1px solid var(--line);
           border-radius:7px; padding:4px 10px; cursor:pointer; white-space:nowrap; }
  button:hover { border-color:var(--muted); }
  .hint { position:fixed; right:12px; top:12px; color:var(--muted); font-size:12px; }
  @media (max-width:560px) { .hint { display:none; } }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="panel">
  <h1 id="name"></h1>
  <div class="dims" id="dims"></div>
  <div id="check"></div>
</div>
<div class="hint">ドラッグ: 回転 / ホイール・ピンチ: 拡大 / 右ドラッグ・2本指: 移動</div>
<div class="views">
  <button data-v="front">正面</button><button data-v="side">側面</button>
  <button data-v="top">上面</button><button data-v="iso">アイソメ</button>
</div>
<script>
const DATA = __DATA__;

function b64(s, T) { const b = atob(s), u = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return new T(u.buffer); }
const pos = b64(DATA.positions, Float32Array), idx = b64(DATA.indices, Uint32Array);

// ---- 情報パネル ----
document.getElementById('name').textContent = DATA.name;
const e = DATA.extents;
document.getElementById('dims').textContent =
  `${e[0].toFixed(1)} × ${e[1].toFixed(1)} × ${e[2].toFixed(1)} mm ・ 体積 ${(DATA.volume/1000).toFixed(1)} cm³ ・ ${DATA.triangles.toLocaleString()} 面`;
if (DATA.check) {
  const s = DATA.check.summary, el = document.getElementById('check');
  const cls = s.errors ? 'err' : (s.warnings ? 'warn' : 'ok');
  el.innerHTML = `<span class="badge ${cls}">check_stl: ${s.errors ? 'NG' : 'PASS'}（ERROR ${s.errors} / WARN ${s.warnings}）</span>`;
  const d = document.createElement('details'); d.innerHTML = '<summary>チェック詳細</summary>';
  const ul = document.createElement('ul');
  for (const it of DATA.check.checks) {
    const li = document.createElement('li'), c = {OK:'ok', WARN:'warn', ERROR:'err'}[it.level] || '';
    li.innerHTML = `<b class="${c}"></b>`; li.firstChild.textContent = it.level;
    li.appendChild(document.createTextNode(it.message)); ul.appendChild(li);
  }
  d.appendChild(ul); el.appendChild(d);
}

// ---- WebGL2 ----
const canvas = document.getElementById('c');
const gl = canvas.getContext('webgl2', {antialias: true});
if (!gl) { document.body.insertAdjacentHTML('beforeend', '<p style="position:fixed;top:45%;width:100%;text-align:center">WebGL2 に対応したブラウザで開いてください</p>'); throw 0; }

function prog(vs, fs) {
  const p = gl.createProgram();
  for (const [t, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw gl.getShaderInfoLog(s);
    gl.attachShader(p, s);
  }
  gl.linkProgram(p); return p;
}
const meshProg = prog(`#version 300 es
layout(location = 0) in vec3 p; uniform mat4 mvp; out vec3 w;
void main(){ w = p; gl_Position = mvp * vec4(p, 1.0); }`,
`#version 300 es
precision highp float; in vec3 w; uniform vec3 eye; uniform vec3 base; out vec4 o;
void main(){
  vec3 n = normalize(cross(dFdx(w), dFdy(w)));
  vec3 v = normalize(eye - w); if (dot(n, v) < 0.0) n = -n;
  float head = max(dot(n, v), 0.0);
  float key = max(dot(n, normalize(vec3(0.4, -0.6, 0.9))), 0.0);
  vec3 c = base * (0.28 + 0.45 * head + 0.35 * key) + vec3(0.12) * pow(head, 24.0);
  o = vec4(c, 1.0);
}`);
const lineProg = prog(`#version 300 es
layout(location = 0) in vec3 p; uniform mat4 mvp; void main(){ gl_Position = mvp * vec4(p, 1.0); }`,
`#version 300 es
precision mediump float; uniform vec4 col; out vec4 o; void main(){ o = col; }`);

function buf(target, data) { const b = gl.createBuffer(); gl.bindBuffer(target, b); gl.bufferData(target, data, gl.STATIC_DRAW); return b; }
const meshVao = gl.createVertexArray(); gl.bindVertexArray(meshVao);
buf(gl.ARRAY_BUFFER, pos); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
buf(gl.ELEMENT_ARRAY_BUFFER, idx);

// ベッドのグリッド（10 mm 間隔、モデルの外形 + 余白）
const [mn, mx] = DATA.bounds, pad = 20, step = 10;
const gx0 = Math.floor((mn[0] - pad) / step) * step, gx1 = Math.ceil((mx[0] + pad) / step) * step;
const gy0 = Math.floor((mn[1] - pad) / step) * step, gy1 = Math.ceil((mx[1] + pad) / step) * step;
const gz = mn[2] - 0.05, g = [];
for (let x = gx0; x <= gx1; x += step) g.push(x, gy0, gz, x, gy1, gz);
for (let y = gy0; y <= gy1; y += step) g.push(gx0, y, gz, gx1, y, gz);
const gridVao = gl.createVertexArray(); gl.bindVertexArray(gridVao);
buf(gl.ARRAY_BUFFER, new Float32Array(g)); gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
const gridCount = g.length / 3;
// 軸（X 赤 / Y 緑 / Z 青）
const L = Math.max(...DATA.extents) * 0.25;
const axes = [[1,0,0],[0,1,0],[0,0,1]].map((d, i) => {
  const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
  buf(gl.ARRAY_BUFFER, new Float32Array([gx0, gy0, gz, gx0 + d[0]*L, gy0 + d[1]*L, gz + d[2]*L]));
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0); return vao; });
const axisCol = [[0.85,0.25,0.25,1],[0.25,0.7,0.3,1],[0.3,0.45,0.9,1]];

// ---- カメラ（Z 上向き） ----
const center = [(mn[0]+mx[0])/2, (mn[1]+mx[1])/2, (mn[2]+mx[2])/2];
const radius = Math.hypot(...DATA.extents) / 2;
const VIEWS = { front:[-90, 0], side:[0, 0], top:[-90, 89.9], iso:[-60, 30] };
const FOV = 35 * Math.PI / 180;
// 縦横の狭い方の画角に外接球が収まる距離（余白 15%）
function fitDist() { const asp = canvas.clientWidth / canvas.clientHeight;
  const half = Math.min(FOV / 2, Math.atan(Math.tan(FOV / 2) * asp)); return radius * 1.15 / Math.sin(half); }
let az = -60, el = 30, dist = fitDist(), target = center.slice();
function setView(v) { [az, el] = VIEWS[v]; dist = fitDist(); target = center.slice(); draw(); }
document.querySelectorAll('button[data-v]').forEach(b => b.onclick = () => setView(b.dataset.v));

const sub = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm = a => { const l = Math.hypot(...a); return [a[0]/l, a[1]/l, a[2]/l]; };
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
function mul(a, b) { const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k*4 + r] * b[c*4 + k]; o[c*4 + r] = s; } return o; }
function eyePos() { const a = az * Math.PI/180, e = el * Math.PI/180;
  return [target[0] + dist*Math.cos(e)*Math.cos(a), target[1] + dist*Math.cos(e)*Math.sin(a), target[2] + dist*Math.sin(e)]; }
function basis() { const f = norm(sub(target, eyePos())); const r = norm(cross(f, [0,0,1])); return [f, r, cross(r, f)]; }
function matrices() {
  const eye = eyePos(), [f, r, u] = basis();
  const view = new Float32Array([r[0],u[0],-f[0],0, r[1],u[1],-f[1],0, r[2],u[2],-f[2],0,
                                 -dot(r,eye), -dot(u,eye), dot(f,eye), 1]);
  const asp = canvas.width / canvas.height, t = 1/Math.tan(FOV/2);
  const n = Math.max(dist - radius*4, dist*0.01), fa = dist + radius*4;
  const proj = new Float32Array([t/asp,0,0,0, 0,t,0,0, 0,0,(fa+n)/(n-fa),-1, 0,0,2*fa*n/(n-fa),0]);
  return [mul(proj, view), eye];
}

const dark = matchMedia('(prefers-color-scheme: dark)').matches;
function draw() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  gl.viewport(0, 0, w, h);
  const bg = dark ? [0.086, 0.094, 0.114] : [0.957, 0.961, 0.969];
  gl.clearColor(...bg, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.enable(gl.DEPTH_TEST);
  const [mvp, eye] = matrices();
  gl.useProgram(lineProg);
  gl.uniformMatrix4fv(gl.getUniformLocation(lineProg, 'mvp'), false, mvp);
  gl.uniform4fv(gl.getUniformLocation(lineProg, 'col'), dark ? [0.25,0.27,0.31,1] : [0.8,0.82,0.86,1]);
  gl.bindVertexArray(gridVao); gl.drawArrays(gl.LINES, 0, gridCount);
  axes.forEach((vao, i) => { gl.uniform4fv(gl.getUniformLocation(lineProg, 'col'), axisCol[i]);
    gl.bindVertexArray(vao); gl.drawArrays(gl.LINES, 0, 2); });
  gl.useProgram(meshProg);
  gl.uniformMatrix4fv(gl.getUniformLocation(meshProg, 'mvp'), false, mvp);
  gl.uniform3fv(gl.getUniformLocation(meshProg, 'eye'), eye);
  gl.uniform3fv(gl.getUniformLocation(meshProg, 'base'), [0.45, 0.72, 0.85]);
  gl.bindVertexArray(meshVao); gl.drawElements(gl.TRIANGLES, idx.length, gl.UNSIGNED_INT, 0);
}

// ---- 操作（マウス・タッチ共通の Pointer Events） ----
const pts = new Map(); let pinch = null;
canvas.addEventListener('contextmenu', ev => ev.preventDefault());
canvas.addEventListener('pointerdown', ev => { canvas.setPointerCapture(ev.pointerId);
  pts.set(ev.pointerId, {x: ev.clientX, y: ev.clientY, pan: ev.button === 2 || ev.shiftKey}); pinch = null; });
canvas.addEventListener('pointerup', ev => { pts.delete(ev.pointerId); pinch = null; });
canvas.addEventListener('pointercancel', ev => { pts.delete(ev.pointerId); pinch = null; });
function pan(dx, dy) { const [, r, u] = basis(), k = dist * 0.0015;
  for (let i = 0; i < 3; i++) target[i] += (-dx * r[i] + dy * u[i]) * k; }
canvas.addEventListener('pointermove', ev => {
  const p = pts.get(ev.pointerId); if (!p) return;
  const dx = ev.clientX - p.x, dy = ev.clientY - p.y;
  if (pts.size === 1) {
    if (p.pan) pan(dx, dy); else { az -= dx * 0.4; el = Math.max(-89.9, Math.min(89.9, el + dy * 0.4)); }
  } else if (pts.size === 2) {
    const [a, b] = [...pts.values()];
    const d0 = Math.hypot(a.x - b.x, a.y - b.y); p.x = ev.clientX; p.y = ev.clientY;
    const d1 = Math.hypot(a.x - b.x, a.y - b.y);
    if (d0 > 0) dist *= d0 / d1; pan(dx / 2, dy / 2); draw(); return;
  }
  p.x = ev.clientX; p.y = ev.clientY; draw();
});
canvas.addEventListener('wheel', ev => { ev.preventDefault(); dist *= Math.exp(ev.deltaY * 0.001); draw(); }, {passive: false});
addEventListener('resize', draw);
draw();
</script>
</body>
</html>
"""


def build_html(stl: Path) -> str:
    mesh = trimesh.load(stl, force="mesh", process=True)
    mesh.merge_vertices()
    check_path = stl.with_suffix(".check.json")
    check = json.loads(check_path.read_text(encoding="utf-8")) if check_path.is_file() else None
    data = {
        "name": stl.stem,
        "bounds": mesh.bounds.tolist(),
        "extents": mesh.extents.tolist(),
        "volume": float(mesh.volume) if mesh.is_watertight else 0.0,
        "triangles": int(len(mesh.faces)),
        "positions": base64.b64encode(mesh.vertices.astype("<f4").tobytes()).decode(),
        "indices": base64.b64encode(mesh.faces.astype("<u4").tobytes()).decode(),
        "check": check,
    }
    title = f"{stl.stem} ビューア"
    return TEMPLATE.replace("__TITLE__", title).replace("__DATA__", json.dumps(data, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="STL から単体で動く 3D ビューア HTML を生成する")
    ap.add_argument("stl", type=Path)
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args(argv)
    if not args.stl.is_file():
        print(f"ファイルが無い: {args.stl}", file=sys.stderr)
        return 1
    out = args.output or args.stl.with_name(f"{args.stl.stem}-viewer.html")
    out.write_text(build_html(args.stl), encoding="utf-8")
    print(f"viewer: {out}（{out.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
