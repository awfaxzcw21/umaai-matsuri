const fs = require('node:fs');
const { DatabaseSync } = require('node:sqlite');
const DATA = 'E:/HJM/_ref01/matsuri-ref-01/data/';
const db = new DatabaseSync(DATA + 'master.mdb', { readOnly: true });
const texts = JSON.parse(fs.readFileSync(DATA + 'text_data_dict.json', 'utf8'));
const nm = id => (texts['47'] || {})[String(id)] || '?';

console.log('=== grade_value stats by rarity ===');
for (const r of db.prepare(`SELECT rarity, COUNT(*) n, MIN(grade_value) mn, MAX(grade_value) mx,
  ROUND(AVG(grade_value),1) avg FROM skill_data GROUP BY rarity ORDER BY rarity`).all()) console.log(' ' + JSON.stringify(r));

console.log('\n=== grade_value distribution (all 2223) ===');
const d = db.prepare('SELECT grade_value v, COUNT(*) n FROM skill_data GROUP BY grade_value ORDER BY v').all();
console.log(' distinct=' + d.length + '  ' + d.map(x => `${x.v}:${x.n}`).join('  ').slice(0, 1200));

console.log('\n=== extremes with names ===');
const hi = db.prepare('SELECT id, grade_value, rarity FROM skill_data ORDER BY grade_value DESC LIMIT 8').all();
const lo = db.prepare('SELECT id, grade_value, rarity FROM skill_data ORDER BY grade_value ASC LIMIT 8').all();
for (const r of [...hi, ...lo]) console.log(`  ${r.grade_value}  rar=${r.rarity}  ${nm(r.id)}`);

console.log('\n=== tables/columns that could be a score ===');
for (const t of db.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").all()) {
  const cols = db.prepare(`PRAGMA table_info(${t.name})`).all().map(c => c.name);
  const hit = cols.filter(c => /score|grade|point|_val|rank|power/i.test(c));
  if (hit.length && /skill|single_mode|rank|score|point/i.test(t.name))
    console.log(`  ${t.name}: ${hit.join(', ')}`);
}
