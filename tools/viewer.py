"""単体で動く 3D ビューア HTML を生成する（外部ライブラリ・CDN 不使用、オフライン可）。

使い方:
    uv run tools/viewer.py models/<name>      # model.py から（パーツ別表示・可動部の操作に対応）
    uv run tools/viewer.py <stl>              # STL 1 つから
    オプション: -o out.html

- 既定の出力先は out/<name>-viewer.html（STL 指定時は STL と同じ場所）
- out/<name>.check.json（複数パーツなら <name>-<part>.check.json）があれば、チェック結果も表示する
- 操作: ドラッグで回転、ホイール／ピンチで拡大、右ドラッグ／2 本指で移動、ボタンで視点切り替え
- model.py に `motions` があれば、可動部を角度スライダーで動かし、振り子の揺れを再生し、
  角度ごとの他パーツとの干渉（build123d で事前計算）を表示する。パーツの表示／半透明、断面も切り替えられる
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import numpy as np

TESS_TOLERANCE = 0.02         # mm
TESS_ANGULAR = 0.2            # rad
CLEARANCE_STEP = 10           # 干渉チェックの角度刻み（度）
GRAVITY = 9810.0              # mm/s²
PENDULUM_DAMPING = 0.12       # 減衰比（見た目用の仮定。軸の摩擦で決まる実値は不明）
PALETTE = [(0.45, 0.72, 0.85), (0.93, 0.62, 0.35), (0.55, 0.78, 0.47), (0.78, 0.55, 0.85), (0.9, 0.8, 0.4)]

TEMPLATE = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --bg:#f4f5f7; --panel:#ffffffe6; --text:#1d2330; --muted:#5b6475; --line:#d8dce3;
          --ok:#1f7a4d; --warn:#9a6700; --err:#b42318; --accent:#2f6fde; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16181d; --panel:#23262de6; --text:#e8eaf0; --muted:#a2a9b8; --line:#353a44;
            --ok:#4cc38a; --warn:#e0b23b; --err:#f07167; --accent:#7aa7ff; }
  }
  html, body { margin:0; height:100%; background:var(--bg); color:var(--text);
               font:14px/1.5 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Segoe UI", sans-serif; }
  canvas { position:fixed; inset:0; width:100%; height:100%; touch-action:none; display:block; }
  .panel { position:fixed; background:var(--panel); border:1px solid var(--line); border-radius:10px;
           padding:10px 12px; backdrop-filter:blur(6px); box-sizing:border-box; }
  #info { left:12px; top:12px; max-width:min(360px, calc(100% - 24px)); }
  #ctrl { right:12px; top:12px; width:min(300px, calc(100% - 24px)); max-height:calc(100% - 90px); overflow:auto; }
  @media (max-width:700px) { #ctrl { top:auto; bottom:64px; right:12px; max-height:45%; } }
  h1 { font-size:15px; margin:0 0 2px; }
  h2 { font-size:13px; margin:8px 0 4px; color:var(--muted); font-weight:600; }
  h2:first-of-type { margin-top:0; }
  .dims { color:var(--muted); font-variant-numeric:tabular-nums; }
  .badge { display:inline-block; font-weight:600; padding:0 8px; border-radius:999px; margin-top:6px;
           border:1px solid currentColor; }
  .ok { color:var(--ok); } .warn { color:var(--warn); } .err { color:var(--err); }
  details { margin-top:6px; } summary { cursor:pointer; color:var(--muted); }
  #ctrl > details { margin:0; } #ctrl > details > summary { font-weight:600; color:var(--text); }
  ul { margin:4px 0 0; padding-left:0; list-style:none; font-size:12px; max-height:45vh; overflow:auto; }
  li { margin:2px 0; } li b { display:inline-block; width:44px; }
  .row { display:flex; align-items:center; gap:8px; margin:3px 0; font-size:13px; }
  .row label { display:flex; align-items:center; gap:4px; cursor:pointer; }
  .sw { width:10px; height:10px; border-radius:2px; display:inline-block; flex:none; }
  input[type=range] { flex:1; min-width:0; accent-color:var(--accent); }
  .num { font-variant-numeric:tabular-nums; min-width:52px; text-align:right; }
  .note { font-size:12px; color:var(--muted); }
  .views { position:fixed; left:50%; bottom:max(12px, env(safe-area-inset-bottom)); transform:translateX(-50%);
           display:flex; gap:6px; background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:6px; }
  button, select { font:inherit; color:var(--text); background:transparent; border:1px solid var(--line);
           border-radius:7px; padding:4px 10px; cursor:pointer; white-space:nowrap; }
  button:hover { border-color:var(--muted); }
  button.primary { border-color:var(--accent); color:var(--accent); font-weight:600; }
</style>
</head>
<body>
<canvas id="c"></canvas>
<div class="panel" id="info">
  <h1 id="name"></h1>
  <div class="dims" id="dims"></div>
  <div id="check"></div>
</div>
<div class="panel" id="ctrl"><details id="ctrlBox" open><summary>操作</summary><div id="ctrlBody"></div></details></div>
<div class="views">
  <button data-v="front">正面</button><button data-v="side">側面</button>
  <button data-v="top">上面</button><button data-v="iso">アイソメ</button>
</div>
<script>
const DATA = __DATA__;
const $ = (tag, attrs = {}, text) => { const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; return e; };
function b64(s, T) { const b = atob(s), u = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return new T(u.buffer); }

// ---- 情報パネル ----
document.getElementById('name').textContent = DATA.name;
const E = DATA.extents;
document.getElementById('dims').textContent =
  `${E[0].toFixed(1)} × ${E[1].toFixed(1)} × ${E[2].toFixed(1)} mm ・ 体積 ${(DATA.volume/1000).toFixed(1)} cm³` +
  (DATA.parts.length > 1 ? ` ・ ${DATA.parts.length} パーツ` : '');
if (DATA.check) {
  const s = DATA.check.summary, el = document.getElementById('check');
  const cls = s.errors ? 'err' : (s.warnings ? 'warn' : 'ok');
  el.appendChild($('span', {class: `badge ${cls}`}, `check_stl: ${s.errors ? 'NG' : 'PASS'}（ERROR ${s.errors} / WARN ${s.warnings}）`));
  const d = $('details'); d.appendChild($('summary', {}, 'チェック詳細'));
  const ul = $('ul');
  for (const it of DATA.check.checks) {
    const li = $('li'); li.appendChild($('b', {class: {OK:'ok', WARN:'warn', ERROR:'err'}[it.level] || ''}, it.level));
    li.appendChild(document.createTextNode(it.message)); ul.appendChild(li);
  }
  d.appendChild(ul); el.appendChild(d);
}

// ---- WebGL2 ----
const canvas = document.getElementById('c');
const gl = canvas.getContext('webgl2', {antialias: true});
if (!gl) { document.body.appendChild($('p', {style: 'position:fixed;top:45%;width:100%;text-align:center'}, 'WebGL2 に対応したブラウザで開いてください')); throw 0; }
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
layout(location = 0) in vec3 p; uniform mat4 vp; uniform mat4 model; out vec3 w;
void main(){ vec4 wp = model * vec4(p, 1.0); w = wp.xyz; gl_Position = vp * wp; }`,
`#version 300 es
precision highp float; in vec3 w; uniform vec3 eye; uniform vec3 base; uniform float alpha;
uniform float clipX; uniform bool useClip; out vec4 o;
void main(){
  if (useClip && w.x > clipX) discard;
  vec3 n = normalize(cross(dFdx(w), dFdy(w)));
  vec3 v = normalize(eye - w); bool back = dot(n, v) < 0.0; if (back) n = -n;
  float head = max(dot(n, v), 0.0);
  float key = max(dot(n, normalize(vec3(0.4, -0.6, 0.9))), 0.0);
  vec3 c = base * (0.28 + 0.45 * head + 0.35 * key) + vec3(0.12) * pow(head, 24.0);
  if (useClip && back) c = base * 0.35;   // 断面で見える内側は暗く
  o = vec4(c, alpha);
}`);
const lineProg = prog(`#version 300 es
layout(location = 0) in vec3 p; uniform mat4 vp; void main(){ gl_Position = vp * vec4(p, 1.0); }`,
`#version 300 es
precision mediump float; uniform vec4 col; out vec4 o; void main(){ o = col; }`);
const U = (p, n) => gl.getUniformLocation(p, n);

function vao(positions, indices) {
  const a = gl.createVertexArray(); gl.bindVertexArray(a);
  const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
  if (indices) { const e = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, e); gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW); }
  return a;
}
const parts = DATA.parts.map(p => {
  const idx = b64(p.indices, Uint32Array);
  return { ...p, vao: vao(b64(p.positions, Float32Array), idx), count: idx.length,
           visible: true, ghost: false, angle: 0, vel: 0 };
});

// ベッドのグリッド（10 mm 間隔）と軸（X 赤 / Y 緑 / Z 青）
const [mn, mx] = DATA.bounds, pad = 20, step = 10;
const gx0 = Math.floor((mn[0] - pad) / step) * step, gx1 = Math.ceil((mx[0] + pad) / step) * step;
const gy0 = Math.floor((mn[1] - pad) / step) * step, gy1 = Math.ceil((mx[1] + pad) / step) * step;
const gz = mn[2] - 0.05, g = [];
for (let x = gx0; x <= gx1; x += step) g.push(x, gy0, gz, x, gy1, gz);
for (let y = gy0; y <= gy1; y += step) g.push(gx0, y, gz, gx1, y, gz);
const gridVao = vao(new Float32Array(g)), gridCount = g.length / 3;
const L = Math.max(...E) * 0.25;
const axes = [[1,0,0],[0,1,0],[0,0,1]].map(d => vao(new Float32Array([gx0, gy0, gz, gx0 + d[0]*L, gy0 + d[1]*L, gz + d[2]*L])));
const axisCol = [[0.85,0.25,0.25,1],[0.25,0.7,0.3,1],[0.3,0.45,0.9,1]];

// ---- 行列 ----
const sub = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const cross = (a, b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm = a => { const l = Math.hypot(...a); return [a[0]/l, a[1]/l, a[2]/l]; };
const dot = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
function mul(a, b) { const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0; for (let k = 0; k < 4; k++) s += a[k*4 + r] * b[c*4 + k]; o[c*4 + r] = s; } return o; }
const I4 = new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]);
const T4 = t => new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, t[0],t[1],t[2],1]);
function R4(u, deg) { const t = deg * Math.PI / 180, c = Math.cos(t), s = Math.sin(t), C = 1 - c, [x, y, z] = norm(u);
  return new Float32Array([c+x*x*C, y*x*C+z*s, z*x*C-y*s, 0,  x*y*C-z*s, c+y*y*C, z*y*C+x*s, 0,
                           x*z*C+y*s, y*z*C-x*s, c+z*z*C, 0,  0, 0, 0, 1]); }
function modelOf(p) { if (!p.motion || !p.angle) return I4; const o = p.motion.origin;
  return mul(T4(o), mul(R4(p.motion.direction, p.angle), T4([-o[0], -o[1], -o[2]]))); }

// ---- カメラ（Z 上向き） ----
const center = [(mn[0]+mx[0])/2, (mn[1]+mx[1])/2, (mn[2]+mx[2])/2];
const radius = Math.hypot(...E) / 2, FOV = 35 * Math.PI / 180;
const VIEWS = { front:[-90, 0], side:[0, 0], top:[-90, 89.9], iso:[-60, 30] };
function fitDist() { const asp = canvas.clientWidth / canvas.clientHeight;
  const half = Math.min(FOV / 2, Math.atan(Math.tan(FOV / 2) * asp)); return radius * 1.15 / Math.sin(half); }
let az = -60, el = 30, dist = fitDist(), target = center.slice();
function setView(v) { [az, el] = VIEWS[v]; dist = fitDist(); target = center.slice(); draw(); }
document.querySelectorAll('button[data-v]').forEach(b => b.onclick = () => setView(b.dataset.v));
function eyePos() { const a = az * Math.PI/180, e = el * Math.PI/180;
  return [target[0] + dist*Math.cos(e)*Math.cos(a), target[1] + dist*Math.cos(e)*Math.sin(a), target[2] + dist*Math.sin(e)]; }
function basis() { const f = norm(sub(target, eyePos())); const r = norm(cross(f, [0,0,1])); return [f, r, cross(r, f)]; }
function viewProj() {
  const eye = eyePos(), [f, r, u] = basis();
  const view = new Float32Array([r[0],u[0],-f[0],0, r[1],u[1],-f[1],0, r[2],u[2],-f[2],0, -dot(r,eye), -dot(u,eye), dot(f,eye), 1]);
  const asp = canvas.width / canvas.height, t = 1/Math.tan(FOV/2);
  const n = Math.max(dist - radius*4, dist*0.01), fa = dist + radius*4;
  const proj = new Float32Array([t/asp,0,0,0, 0,t,0,0, 0,0,(fa+n)/(n-fa),-1, 0,0,2*fa*n/(n-fa),0]);
  return [mul(proj, view), eye];
}

// ---- 描画 ----
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
let clip = { on: false, x: 0 };
function drawPart(p, vp, eye, alpha) {
  gl.uniformMatrix4fv(U(meshProg, 'model'), false, modelOf(p));
  gl.uniform3fv(U(meshProg, 'base'), p.color); gl.uniform1f(U(meshProg, 'alpha'), alpha);
  gl.bindVertexArray(p.vao); gl.drawElements(gl.TRIANGLES, p.count, gl.UNSIGNED_INT, 0);
}
// 描画要求はフレームごとにまとめる（同じフレームで何度呼んでも 1 回だけ描く）
let drawPending = false;
function draw() { if (!drawPending) { drawPending = true; requestAnimationFrame(() => { drawPending = false; render(); }); } }
function render() {
  const dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.round(canvas.clientWidth * dpr), h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  gl.viewport(0, 0, w, h);
  gl.clearColor(...(dark ? [0.086, 0.094, 0.114] : [0.957, 0.961, 0.969]), 1);
  // 半透明パスで depthMask(false) にしたままだと深度バッファが消去されず、次の描画が全部隠れるため先に戻す
  gl.depthMask(true); gl.disable(gl.BLEND); gl.enable(gl.DEPTH_TEST);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  const [vp, eye] = viewProj();
  gl.useProgram(lineProg); gl.uniformMatrix4fv(U(lineProg, 'vp'), false, vp);
  gl.uniform4fv(U(lineProg, 'col'), dark ? [0.25,0.27,0.31,1] : [0.8,0.82,0.86,1]);
  gl.bindVertexArray(gridVao); gl.drawArrays(gl.LINES, 0, gridCount);
  axes.forEach((a, i) => { gl.uniform4fv(U(lineProg, 'col'), axisCol[i]); gl.bindVertexArray(a); gl.drawArrays(gl.LINES, 0, 2); });
  gl.useProgram(meshProg);
  gl.uniformMatrix4fv(U(meshProg, 'vp'), false, vp); gl.uniform3fv(U(meshProg, 'eye'), eye);
  gl.uniform1i(U(meshProg, 'useClip'), clip.on ? 1 : 0); gl.uniform1f(U(meshProg, 'clipX'), clip.x);
  for (const p of parts) if (p.visible && !p.ghost) drawPart(p, vp, eye, 1);
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
  for (const p of parts) if (p.visible && p.ghost) drawPart(p, vp, eye, 0.28);
}

// ---- 操作パネル ----
const body = document.getElementById('ctrlBody');
if (matchMedia('(max-width:700px)').matches) document.getElementById('ctrlBox').open = false;
const hex = c => '#' + c.map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
if (parts.length > 1) {
  body.appendChild($('h2', {}, 'パーツ'));
  for (const p of parts) {
    const row = $('div', {class: 'row'});
    const sw = $('span', {class: 'sw', style: `background:${hex(p.color)}`});
    const vis = $('input', {type: 'checkbox', checked: ''}); vis.onchange = () => { p.visible = vis.checked; draw(); };
    const gh = $('input', {type: 'checkbox'}); gh.onchange = () => { p.ghost = gh.checked; draw(); };
    const l1 = $('label'); l1.append(vis, sw, document.createTextNode(p.label));
    const l2 = $('label', {class: 'note'}); l2.append(gh, document.createTextNode('半透明'));
    row.append(l1, l2); body.appendChild(row);
  }
}
let anim = null, last = 0;
function clearanceText(p) {
  const tbl = p.clearance; if (!tbl || !tbl.length) return null;
  const s = tbl.reduce((a, b) => Math.abs(b.angle - p.angle) < Math.abs(a.angle - p.angle) ? b : a);
  const bad = Object.entries(s.overlap).filter(([, v]) => v > 1e-3);
  return { ok: !bad.length, text: bad.length
    ? '干渉あり: ' + bad.map(([k, v]) => `${DATA.labels[k] || k} ${v.toFixed(2)} mm³`).join('、')
    : '干渉なし（' + Object.keys(s.overlap).map(k => DATA.labels[k] || k).join('・') + '）', at: s.angle };
}
for (const p of parts.filter(p => p.motion)) {
  const m = p.motion;
  body.appendChild($('h2', {}, `${p.label} の回転`));
  const row = $('div', {class: 'row'});
  const rng = $('input', {type: 'range', min: m.range[0], max: m.range[1], step: 1, value: 0});
  const num = $('span', {class: 'num'}, '0°');
  row.append(rng, num); body.appendChild(row);
  const clr = $('div', {class: 'note clr'}); body.appendChild(clr);
  const upd = () => { rng.value = Math.round(p.angle); num.textContent = `${Math.round(p.angle)}°`;
    const c = clearanceText(p); if (c) { clr.className = 'note clr ' + (c.ok ? 'ok' : 'err');
      clr.textContent = `${c.text}  ※ ${c.at}° の計算値（${DATA.clearanceStep}° ごと）`; } };
  p.update = upd; upd();
  rng.oninput = () => { anim = null; p.angle = +rng.value; p.vel = 0; upd(); draw(); };
  const btns = $('div', {class: 'row'});
  if (m.pendulum) {
    const go = $('button', {class: 'primary'}, '揺らす');
    const speed = $('select'); for (const [v, t] of [[1, '実時間'], [0.25, '1/4 スロー'], [0.1, '1/10 スロー']]) speed.appendChild($('option', {value: v}, t));
    speed.value = 0.25;
    go.onclick = () => { if (Math.abs(p.angle) < 5) p.angle = 60; p.vel = 0; anim = { p, speed: +speed.value }; last = 0; requestAnimationFrame(tick); };
    btns.append(go, speed);
    body.appendChild(btns);
    body.appendChild($('div', {class: 'note'},
      `手を離したあと自重で戻る様子（周期 ${m.period.toFixed(2)} 秒・形状から計算。減衰は見た目用の仮定）`));
  }
  const zero = $('button', {}, '0° に戻す'); zero.onclick = () => { anim = null; p.angle = 0; p.vel = 0; upd(); draw(); };
  btns.appendChild(zero); if (!btns.parentNode) body.appendChild(btns);
}
function tick(ts) {
  if (!anim) return;
  const p = anim.p, m = p.motion, w0 = 2 * Math.PI / m.period;
  let dt = last ? Math.min((ts - last) / 1000, 0.05) : 0; last = ts; dt *= anim.speed;
  const n = 20, h = dt / n;
  let th = p.angle * Math.PI / 180, om = p.vel;
  for (let i = 0; i < n; i++) { om += (-w0 * w0 * Math.sin(th) - 2 * m.damping * w0 * om) * h; th += om * h; }
  const lo = m.range[0] * Math.PI / 180, hi = m.range[1] * Math.PI / 180;
  if (th < lo) { th = lo; om = 0; } if (th > hi) { th = hi; om = 0; }
  p.angle = th * 180 / Math.PI; p.vel = om; p.update(); draw();
  if (Math.abs(p.angle) < 0.2 && Math.abs(om) < 0.02) { p.angle = 0; p.vel = 0; p.update(); draw(); anim = null; return; }
  requestAnimationFrame(tick);
}
// 断面（X 方向の位置で切る）
{
  body.appendChild($('h2', {}, '断面'));
  const row = $('div', {class: 'row'});
  const on = $('input', {type: 'checkbox'}); const lab = $('label'); lab.append(on, document.createTextNode('X で切る'));
  const rng = $('input', {type: 'range', min: mn[0].toFixed(1), max: mx[0].toFixed(1), step: 0.5, value: DATA.sectionX});
  const num = $('span', {class: 'num'}, `${(+DATA.sectionX).toFixed(1)}`);
  clip.x = +DATA.sectionX;
  // 切り口が正面に見えるよう、オンにしたら +X 側（側面）からの視点にする
  // 可動部があれば軸まわりに寄る
  const pivot = parts.find(p => p.motion);
  const enable = () => { if (clip.on) return; clip.on = on.checked = true; az = 0; el = 10;
    if (pivot) { const o = pivot.motion.origin; target = [clip.x, o[1], o[2]]; dist = radius * 0.9; } };
  on.onchange = () => { if (on.checked) enable(); else clip.on = false; draw(); };
  rng.oninput = () => { clip.x = +rng.value; num.textContent = (+rng.value).toFixed(1); enable(); draw(); };
  row.append(lab, rng, num); body.appendChild(row);
  body.appendChild($('div', {class: 'note'}, 'X がこの値より大きい部分を隠し、側面から見る（初期値は軸の位置）'));
}

// ---- 視点操作（マウス・タッチ共通の Pointer Events） ----
const pts = new Map();
canvas.addEventListener('contextmenu', ev => ev.preventDefault());
canvas.addEventListener('pointerdown', ev => { canvas.setPointerCapture(ev.pointerId);
  pts.set(ev.pointerId, {x: ev.clientX, y: ev.clientY, pan: ev.button === 2 || ev.shiftKey}); });
canvas.addEventListener('pointerup', ev => pts.delete(ev.pointerId));
canvas.addEventListener('pointercancel', ev => pts.delete(ev.pointerId));
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


# ---------------------------------------------------------------------------
# データ作成
# ---------------------------------------------------------------------------

def _b64(arr: np.ndarray, dtype: str) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype=dtype).tobytes()).decode()


def load_checks(stl: Path) -> dict | None:
    """<name>.check.json、無ければパーツ別の <name>-<part>.check.json をまとめて返す。"""
    single = stl.with_suffix(".check.json")
    files = [single] if single.is_file() else sorted(stl.parent.glob(f"{stl.stem}-*.check.json"))
    if not files:
        return None
    merged = {"summary": {"errors": 0, "warnings": 0}, "checks": []}
    for f in files:
        c = json.loads(f.read_text(encoding="utf-8"))
        merged["summary"]["errors"] += c["summary"]["errors"]
        merged["summary"]["warnings"] += c["summary"]["warnings"]
        part = f.name.removesuffix(".check.json").removeprefix(f"{stl.stem}-")
        for item in c["checks"]:
            msg = item["message"] if f == single else f"[{part}] {item['message']}"
            merged["checks"].append({**item, "message": msg})
    return merged


def _part_entry(name: str, label: str, verts: np.ndarray, faces: np.ndarray, i: int) -> dict:
    return {"name": name, "label": label, "color": PALETTE[i % len(PALETTE)],
            "positions": _b64(verts, "<f4"), "indices": _b64(faces, "<u4"), "triangles": int(len(faces))}


def data_from_model(model_dir: Path) -> tuple[dict, Path]:
    sys.path.insert(0, str(Path(__file__).parent))
    import export_model   # ROOT（printlib）も sys.path に入る

    from printlib import pendulum_period, sweep_interference

    model_dir = model_dir.resolve()
    mod = export_model.load(model_dir)
    result = mod.result
    children = list(result.children) if getattr(result, "children", None) else []
    shapes = children if len(children) > 1 else [result]
    motions = getattr(mod, "motions", {}) or {}

    parts, labels, named = [], {}, {}
    for i, s in enumerate(shapes):
        key = s.label or f"part{i}"
        named[key] = s
        verts, tris = s.tessellate(TESS_TOLERANCE, TESS_ANGULAR)
        v = np.array([[p.X, p.Y, p.Z] for p in verts])
        f = np.array(tris, dtype=np.int64).reshape(-1, 3)
        label = motions.get(key, {}).get("label") or {"body": "本体", "lid": "蓋フレーム", "flap": "フラップ"}.get(key, key)
        labels[key] = label
        parts.append(_part_entry(key, label, v, f, i))

    for p in parts:
        m = motions.get(p["name"])
        if not m:
            continue
        shape = named[p["name"]]
        others = {k: v for k, v in named.items() if k != p["name"]}
        p["motion"] = {
            "origin": list(m["origin"]), "direction": list(m["direction"]), "range": list(m["range"]),
            "pendulum": bool(m.get("pendulum")), "damping": PENDULUM_DAMPING,
            "period": pendulum_period(shape, m["origin"], m["direction"], GRAVITY) if m.get("pendulum") else 0.0,
        }
        angles = range(int(m["range"][0]), int(m["range"][1]) + 1, CLEARANCE_STEP)
        p["clearance"] = sweep_interference(shape, others, m["origin"], m["direction"], angles)

    bb = result.bounding_box()
    name = model_dir.name
    out_stl = model_dir / "out" / f"{name}.stl"
    first_motion = next(iter(motions.values()), None)
    section_x = 0.0
    if first_motion:   # 可動部の端の少し内側（軸の途中を切る。面と一致するとちらつくので半端な値にする）
        mp = named[next(iter(motions))]
        section_x = float(mp.bounding_box().max.X - 1.5)
    data = {
        "name": name, "bounds": [[bb.min.X, bb.min.Y, bb.min.Z], [bb.max.X, bb.max.Y, bb.max.Z]], "extents": [bb.size.X, bb.size.Y, bb.size.Z],
        "volume": float(sum(s.volume for s in shapes)), "parts": parts, "labels": labels,
        "check": load_checks(out_stl), "clearanceStep": CLEARANCE_STEP, "sectionX": section_x,
    }
    return data, model_dir / "out" / f"{name}-viewer.html"


def data_from_stl(stl: Path) -> tuple[dict, Path]:
    import trimesh

    mesh = trimesh.load(stl, force="mesh", process=True)
    mesh.merge_vertices()
    data = {
        "name": stl.stem, "bounds": mesh.bounds.tolist(), "extents": mesh.extents.tolist(),
        "volume": float(mesh.volume) if mesh.is_watertight else 0.0,
        "parts": [_part_entry(stl.stem, stl.stem, mesh.vertices, mesh.faces, 0)], "labels": {},
        "check": load_checks(stl), "clearanceStep": CLEARANCE_STEP, "sectionX": float(mesh.centroid[0]),
    }
    return data, stl.with_name(f"{stl.stem}-viewer.html")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="単体で動く 3D ビューア HTML を生成する")
    ap.add_argument("source", type=Path, help="models/<name> ディレクトリ、または STL")
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args(argv)
    src = args.source
    if src.is_dir() and (src / "model.py").is_file():
        data, out = data_from_model(src)
    elif src.is_file():
        data, out = data_from_stl(src)
    else:
        print(f"model.py のあるディレクトリか STL を指定: {src}", file=sys.stderr)
        return 1
    out = args.output or out
    out.parent.mkdir(exist_ok=True)
    html = TEMPLATE.replace("__TITLE__", f"{data['name']} ビューア").replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out.write_text(html, encoding="utf-8")
    moving = [p["label"] for p in data["parts"] if p.get("motion")]
    print(f"viewer: {out}（{out.stat().st_size / 1024:.0f} KB、パーツ {len(data['parts'])}"
          + (f"、可動: {', '.join(moving)}" if moving else "") + "）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
