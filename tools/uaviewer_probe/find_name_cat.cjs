const fs = require('node:fs');
const { DatabaseSync } = require('node:sqlite');
const DATA = 'E:/HJM/_ref01/matsuri-ref-01/data/';
const texts = JSON.parse(fs.readFileSync(DATA + 'text_data_dict.json', 'utf8'));
const db = new DatabaseSync(DATA + 'master.mdb', { readOnly: true });

const probeIds = db.prepare('SELECT id FROM skill_data ORDER BY id LIMIT 400').all().map(r => String(r.id));
const hits = [];
for (const [cat, sub] of Object.entries(texts)) {
  let n = 0;
  for (const id of probeIds) if (sub[id] !== undefined) n++;
  if (n > 50) hits.push({ cat, size: Object.keys(sub).length, matched: n, e1: sub[probeIds[0]], e2: sub[probeIds[1]] });
}
console.table(hits.map(h => ({ cat: h.cat, catSize: h.size, matchedOf400: h.matched,
  s1: String(h.e1 || '').replace(/\n/g, '\\n').slice(0, 28), s2: String(h.e2 || '').replace(/\n/g, '\\n').slice(0, 28) })));

console.log('\n--- pt ids present in which cats ---');
for (const id of ['202552', '202061', '10071']) {
  const cats = Object.entries(texts).filter(([, s]) => s[id] !== undefined).map(([c]) => c);
  console.log(id + ' -> cats ' + JSON.stringify(cats) + (cats[0] ? ' | ' + String(texts[cats[0]][id]).replace(/\n/g, '\\n').slice(0, 40) : ''));
}
