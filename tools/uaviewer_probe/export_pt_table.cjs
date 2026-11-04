const fs = require('node:fs');
const { DatabaseSync } = require('node:sqlite');

const DATA = 'E:/HJM/_ref01/matsuri-ref-01/data/';
const texts = JSON.parse(fs.readFileSync(DATA + 'text_data_dict.json', 'utf8'));

// which text category holds skill names: probe a known skill id across categories

const db = new DatabaseSync(DATA + 'master.mdb', { readOnly: true });
const rows = db.prepare(`
  SELECT p.id AS skill_id, p.need_skill_point AS pt, p.status_type, p.status_value, p.solvable_type,
         d.rarity, d.grade_value, d.skill_category, d.tag_id, d.exp_type, d.potential_per_default,
         d.disable_singlemode, d.group_id, d.group_rate, d.unique_skill_id_1, d.unique_skill_id_2
  FROM single_mode_skill_need_point p JOIN skill_data d ON d.id = p.id
  ORDER BY p.need_skill_point, d.rarity, p.id`).all();

const nameCat = '47', descCat = '48';
const out = rows.map(r => ({
  skill_id: r.skill_id,
  name: (texts[nameCat] || {})[String(r.skill_id)] ?? null,
  desc: (texts[descCat] || {})[String(r.skill_id)] ?? null,
  need_skill_point: r.pt,
  rarity: r.rarity,
  grade_value: r.grade_value,
  skill_category: r.skill_category,
  tag_id: r.tag_id,
  exp_type: r.exp_type,
  potential_per_default: r.potential_per_default,
  disable_singlemode: r.disable_singlemode,
  group_id: r.group_id,
  group_rate: r.group_rate,
}));

const named = out.filter(o => o.name).length;
console.log(`\nrows=${out.length} with-name-from-cat47=${named}`);
console.log('sample:', JSON.stringify(out.slice(0, 3), null, 1));

const outFile = 'E:/哈基米分裂/data/skill_need_point_from_master.json';
fs.writeFileSync(outFile, JSON.stringify({
  source: 'E:/HJM/_ref01/matsuri-ref-01/data/master.mdb',
  tables: ['single_mode_skill_need_point', 'skill_data', 'text_data_dict cat47/cat48'],
  note: 'grade_value / exp_type / potential_per_default semantics NOT verified; raw columns only',
  pt_values_seen: [...new Set(out.map(o => o.need_skill_point))].sort((a, b) => a - b),
  rows: out,
}, null, 1));
console.log('written -> ' + outFile);
