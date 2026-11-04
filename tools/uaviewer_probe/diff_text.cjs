const fs = require('node:fs');

const VIEWER = 'C:/Users/PC/AppData/Local/Temp/uv3/out/blob_02_off1179180.json';
const OURS = 'E:/HJM/_ref01/matsuri-ref-01/data/text_data_dict.json';

const v = JSON.parse(fs.readFileSync(VIEWER, 'utf8'));
const o = JSON.parse(fs.readFileSync(OURS, 'utf8'));

function describe(name, obj) {
  const keys = Object.keys(obj);
  const sample = keys[0];
  const val = obj[sample];
  console.log(`\n### ${name}`);
  console.log(`top-level keys: ${keys.length}  first: ${JSON.stringify(keys.slice(0, 8))}`);
  console.log(`value shape: ${Array.isArray(val) ? `array(${val.length})` : typeof val}`);
  console.log(`value sample: ${JSON.stringify(val).slice(0, 300)}`);
  let flat = 0, nested = 0;
  for (const k of keys) {
    if (val && typeof val === 'object' && !Array.isArray(val)) nested++;
  }
  console.log(`nested-object values among first-key check: ${nested}/${keys.length}`);
  return { keys, val };
}

describe('viewer blob', v);
describe('our text_data_dict', o);

// flatten both to "cat:id" -> text
function flatten(obj) {
  const out = new Map();
  for (const [cat, sub] of Object.entries(obj)) {
    if (sub && typeof sub === 'object' && !Array.isArray(sub)) {
      for (const [id, txt] of Object.entries(sub)) {
        if (typeof txt === 'string') out.set(`${cat}:${id}`, txt);
      }
    } else if (typeof sub === 'string') {
      out.set(`_:${cat}`, sub);
    }
  }
  return out;
}

const fv = flatten(v);
const fo = flatten(o);
console.log(`\nviewer flat entries: ${fv.size}`);
console.log(`ours   flat entries: ${fo.size}`);

let onlyViewer = 0, onlyOurs = 0, both = 0, diffText = 0;
const missingKeys = [];
for (const [k, txt] of fv) {
  if (!fo.has(k)) { onlyViewer++; if (missingKeys.length < 12) missingKeys.push([k, txt.slice(0, 60)]); }
  else { both++; if (fo.get(k) !== txt) diffText++; }
}
for (const k of fo.keys()) if (!fv.has(k)) onlyOurs++;
console.log(`only-in-viewer=${onlyViewer} only-in-ours=${onlyOurs} in-both=${both} same-key-diff-text=${diffText}`);

// category-level comparison
const vc = new Set([...fv.keys()].map(k => k.split(':')[0]));
const oc = new Set([...fo.keys()].map(k => k.split(':')[0]));
const missingCats = [...vc].filter(c => !oc.has(c));
console.log(`viewer categories=${vc.size} ours categories=${oc.size} categories-missing-from-ours=${missingCats.length}`);
console.log(`missing cats sample: ${JSON.stringify(missingCats.slice(0, 25))}`);

fs.writeFileSync('C:/Users/PC/AppData/Local/Temp/uv3/viewer_only_ids.txt',
  [...fv.entries()].filter(([k]) => !fo.has(k)).map(([k, t]) => `${k}\t${t.replace(/\n/g, '\\n')}`).join('\n'));
console.log('\nmissing-sample:');
for (const [k, t] of missingKeys) console.log(`  ${k}: ${t}`);
