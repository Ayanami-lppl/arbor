// fetch-wheels.mjs -- 用 DSH 自带 node 从 PyPI 下载 PyInstaller 及其依赖的 wheel
// 用法: node tools/fetch-wheels.mjs  （wheel 存到 ../wheels/）
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PYPI = 'https://pypi.org/pypi';
const WANT = [
  'pyinstaller',
  'pyinstaller-hooks-contrib',
  'altgraph',
  'pefile',
  'packaging',
  'pywin32-ctypes',
  'setuptools',
];
const OUT_DIR = path.join(__dirname, '..', 'wheels');

function cmpVer(a, b) {
  const pa = Array.isArray(a) ? a : String(a).split('.').map(Number);
  const pb = Array.isArray(b) ? b : String(b).split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return x - y;
  }
  return 0;
}

function pyOk(req) {
  if (!req) return true;
  const cur = [3, 14];
  const re = /([<>=!~]+)\s*([0-9.]+)/g;
  let m;
  while ((m = re.exec(req)) !== null) {
    const op = m[1], v = m[2].split('.').map(Number);
    const cmp = cmpVer(cur, v);
    if (op === '>=' && cmp < 0) return false;
    if (op === '>' && cmp <= 0) return false;
    if (op === '<=' && cmp > 0) return false;
    if (op === '<' && cmp >= 0) return false;
    if (op === '==' && cmp !== 0) return false;
    if (op === '!=' && cmp === 0) return false;
    if (op === '~=') { /* 宽松处理：>=v 且 <v+1 主版本 */ if (cmp < 0) return false; }
  }
  return true;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  for (const name of WANT) {
    const url = `${PYPI}/${name}/json`;
    const res = await fetch(url);
    if (!res.ok) { console.error('FETCH-FAIL', name, res.status); continue; }
    const j = await res.json();
    const versions = Object.keys(j.releases).filter(
      (v) => pyOk(j.info.requires_python)).sort(cmpVer).reverse();
    let chosen = null;
    for (const v of versions) {
      const files = j.releases[v].filter((f) => !f.yanked && (
        f.filename.endsWith('py3-none-any.whl') ||
        f.filename.endsWith('py3-none-win_amd64.whl')));
      if (files.length) { chosen = { v, f: files[0] }; break; }
    }
    if (!chosen) { console.error('NO-WHEEL', name); continue; }
    const dest = path.join(OUT_DIR, chosen.f.filename);
    if (fs.existsSync(dest)) { console.log('HAVE', name, chosen.v, chosen.f.filename); continue; }
    const fr = await fetch(chosen.f.url);
    if (!fr.ok) { console.error('DL-FAIL', name, fr.status); continue; }
    const buf = Buffer.from(await fr.arrayBuffer());
    fs.writeFileSync(dest, buf);
    console.log('GOT', name, chosen.v, chosen.f.filename, buf.length);
  }
}

main().then(() => console.log('DONE')).catch((e) => { console.error(e); process.exit(1); });
