// ビューア HTML のブラウザテスト（Playwright + Chromium）。
//
//   node tools/viewer_smoke.mjs models/<name>/out/<name>-viewer.html [--shots DIR]
//
// 確認すること:
//   - JS エラーが出ない
//   - モデルが描画される（背景以外の画素の割合）
//   - 可動部があれば: 半透明 → 「揺らす」→ 止まったあと → ドラッグ、の各時点で描画が消えない
//     （depthMask を戻し忘れて全部消えるバグの再発防止）、角度スライダーと干渉表示が動く、断面が使える
// Playwright はプロジェクト依存ではないので、`npm i -g playwright` 等で入れておく（無ければ終了コード 2 でスキップ）。

import { execSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { resolve, join } from 'node:path';
import { pathToFileURL } from 'node:url';

async function loadPlaywright() {
  try { return await import('playwright'); } catch {}
  try {
    const root = execSync('npm root -g', { encoding: 'utf8' }).trim();
    return await import(pathToFileURL(join(root, 'playwright', 'index.mjs')).href);
  } catch {}
  return null;
}

const args = process.argv.slice(2);
const html = args.find(a => !a.startsWith('--'));
const shotsIdx = args.indexOf('--shots');
const shots = shotsIdx >= 0 ? args[shotsIdx + 1] : null;
if (!html || !existsSync(html)) { console.error('usage: node tools/viewer_smoke.mjs <viewer.html> [--shots DIR]'); process.exit(1); }
if (shots) mkdirSync(shots, { recursive: true });

const pw = await loadPlaywright();
if (!pw) { console.log('SKIP: playwright が見つからない'); process.exit(2); }

// 描画結果を読み出せるよう preserveDrawingBuffer を有効にして開く（読み出しのため。描画の仕組みは同じ）
const browser = await pw.chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
await page.addInitScript(() => {
  const orig = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (type, opts) {
    return orig.call(this, type, type === 'webgl2' ? { ...(opts || {}), preserveDrawingBuffer: true } : opts);
  };
});
await page.goto(pathToFileURL(resolve(html)).href);
await page.waitForTimeout(500);

const coverage = () => page.evaluate(() => {
  const c = document.getElementById('c'), t = document.createElement('canvas');
  t.width = c.width; t.height = c.height;
  const g = t.getContext('2d'); g.drawImage(c, 0, 0);
  const d = g.getImageData(0, 0, t.width, t.height).data, bg = [d[0], d[1], d[2]];   // 左上 = 背景
  let n = 0, tot = 0;
  for (let i = 0; i < d.length; i += 16) { tot++; if (Math.abs(d[i]-bg[0]) + Math.abs(d[i+1]-bg[1]) + Math.abs(d[i+2]-bg[2]) > 30) n++; }
  return 100 * n / tot;
});
const shot = async name => { if (shots) await page.screenshot({ path: join(shots, `${name}.png`) }); };

const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok, detail }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? '  ' + detail : ''}`); };

const base = await coverage(); await shot('initial');
check('描画される', base > 3, `${base.toFixed(1)}%`);
const near = v => Math.abs(v - base) < base * 0.5;   // 表示量が大きく減っていない

const hasMotion = await page.locator('text=揺らす').count() > 0;
const ghosts = page.locator('#ctrlBody .row label.note input');
if (await ghosts.count() > 1) {
  await ghosts.nth(1).check(); await page.waitForTimeout(100);
  const v = await coverage(); check('半透明にしても消えない', near(v), `${v.toFixed(1)}%`); await shot('ghost');
}
if (hasMotion) {
  const slider = page.locator('#ctrlBody input[type=range]').first();
  await slider.fill('50'); await slider.dispatchEvent('input'); await page.waitForTimeout(100);
  const note = (await page.locator('#ctrlBody .clr').first().textContent()) || '';
  check('角度スライダーと干渉表示', /50°/.test(note) && /干渉/.test(note), note.trim().slice(0, 40)); await shot('angle50');
  await page.selectOption('#ctrlBody select', '1');
  await page.click('text=揺らす'); await page.waitForTimeout(200);
  let v = await coverage(); check('揺らしている最中も消えない', near(v), `${v.toFixed(1)}%`); await shot('swinging');
  await page.waitForTimeout(3500);
  const angle = await page.locator('#ctrlBody .num').first().textContent();
  v = await coverage(); check('揺れが止まったあとも消えない', near(v), `${v.toFixed(1)}% / ${angle}`);
  check('揺れが 0° に戻る', angle.trim() === '0°', angle);
}
await page.mouse.move(550, 380); await page.mouse.down(); await page.mouse.move(600, 360, { steps: 4 }); await page.mouse.up();
await page.waitForTimeout(100);
let v = await coverage(); check('ドラッグしても消えない', near(v) || v > 3, `${v.toFixed(1)}%`);
const clip = page.locator('#ctrlBody input[type=checkbox]').last();
await clip.check(); await page.waitForTimeout(150);
v = await coverage(); check('断面表示で描画される', v > 3, `${v.toFixed(1)}%`); await shot('section');

// スマホ幅（ダークモード）
const phone = await browser.newPage({ viewport: { width: 390, height: 780 }, colorScheme: 'dark' });
phone.on('pageerror', e => errors.push('phone: ' + e.message));
await phone.goto(pathToFileURL(resolve(html)).href); await phone.waitForTimeout(400);
if (shots) await phone.screenshot({ path: join(shots, 'phone.png') });

check('JS エラーなし', errors.length === 0, errors.join(' / '));
await browser.close();
const failed = results.filter(r => !r.ok).length;
console.log(`\nviewer smoke: ${failed ? `FAIL (${failed})` : 'PASS'}`);
process.exit(failed ? 1 : 0);
