const fs = require('node:fs');

const src = process.argv[2];
const outDir = process.argv[3];
const minBytes = Number(process.argv[4] || 50000);
const buf = fs.readFileSync(src);

function scan(i) {
  let d = 0, inStr = false, esc = false;
  for (let j = i; j < buf.length; j++) {
    const c = buf[j];
    if (inStr) {
      if (esc) esc = false;
      else if (c === 0x5c) esc = true;
      else if (c === 0x22) inStr = false;
      continue;
    }
    if (c === 0x22) { inStr = true; continue; }
    if (c === 0x7b) d++;
    else if (c === 0x7d) { d--; if (d === 0) return j + 1; }
  }
  return -1;
}

const found = [];
for (let i = 0; i < buf.length; i++) {
  if (buf[i] !== 0x7b) continue;
  const n = buf[i + 1];
  if (!(n === 0x22 || (n >= 0x09 && n <= 0x0d) || n === 0x20 || n === 0x7b)) continue;
  const end = scan(i);
  if (end < 0 || end - i < minBytes) continue;
  const slice = buf.subarray(i, end);
  let obj;
  try { obj = JSON.parse(slice.toString('utf8')); } catch { continue; }
  found.push({ offset: i, bytes: end - i, obj });
}

// drop nested duplicates: keep blobs whose offset is not inside another kept blob
found.sort((a, b) => a.offset - b.offset);
const kept = [];
for (const f of found) {
  const host = kept[kept.length - 1];
  if (host && f.offset + f.bytes <= host.offset + host.bytes) continue;
  kept.push(f);
}

fs.mkdirSync(outDir, { recursive: true });
kept.forEach((f, k) => {
  const name = `${outDir}/blob_${String(k).padStart(2, '0')}_off${f.offset}.json`;
  fs.writeFileSync(name, JSON.stringify(f.obj, null, 1));
  const shape = Array.isArray(f.obj) ? `array(${f.obj.length})` : `keys(${Object.keys(f.obj).length})`;
  console.log(`${name}  bytes=${f.bytes} ${shape}  sample=${JSON.stringify(Object.keys(f.obj).slice(0, 3))}`);
});
console.log('total blobs:', kept.length);
