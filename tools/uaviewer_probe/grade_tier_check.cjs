const fs = require('node:fs');
const { DatabaseSync } = require('node:sqlite');
const DATA = 'E:/HJM/_ref01/matsuri-ref-01/data/';
const db = new DatabaseSync(DATA + 'master.mdb', { readOnly: true });
const texts = JSON.parse(fs.readFileSync(DATA + 'text_data_dict.json', 'utf8'));
const desc = id => (texts['48'] || {})[String(id)] || '';

// does grade_value track the wording tier in the description?
const tiers = [
  ['极大', /极大/], ['大幅', /大幅/], ['中幅', /中幅/],
  ['略微|稍', /略微|稍/], ['脱产|低落|下降|变弱|不利', /下降|变弱|不利|低落/],
];
console.log('=== grade_value vs description wording tier ===');
for (const d of db.prepare('SELECT id, grade_value, rarity FROM skill_data').all()) { }
const rows = db.prepare('SELECT id, grade_value gv, rarity FROM skill_data').all();
for (const [label, re] of tiers) {
  const hit = rows.filter(r => re.test(desc(r.id)));
  if (!hit.length) { console.log(`  ${label}: no match`); continue; }
  const gs = hit.map(h => h.gv).sort((a, b) => a - b);
  const med = gs[Math.floor(gs.length / 2)];
  console.log(`  ${label.padEnd(10)} n=${String(hit.length).padEnd(4)} median=${String(med).padStart(4)} min=${gs[0]} max=${gs[gs.length - 1]}`);
}

console.log('\n=== PT ladder actually in use (disable_singlemode=0) ===');
for (const r of db.prepare(`SELECT p.need_skill_point pt, d.rarity, d.disable_singlemode off, COUNT(*) n
  FROM single_mode_skill_need_point p JOIN skill_data d ON d.id=p.id
  GROUP BY pt, d.rarity, off ORDER BY pt, rarity`).all()) console.log('  ' + JSON.stringify(r));

console.log('\n=== scenario tables present ===');
const t = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'single_mode%'").all().map(r => r.name);
const scen = [...new Set(t.map(n => (n.match(/^single_mode_(\d+)_/) || [, '0'])[0]))].sort();
console.log('  single_mode_* tables: ' + t.length + '  scenario prefixes: ' + JSON.stringify(scen));
console.log('  ' + t.filter(n => /^single_mode_(14|15)_/.test(n)).join('\n  '));
