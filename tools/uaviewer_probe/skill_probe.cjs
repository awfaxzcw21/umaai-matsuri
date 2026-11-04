const fs = require('node:fs');
const { DatabaseSync } = require('node:sqlite');

const o = JSON.parse(fs.readFileSync('E:/HJM/_ref01/matsuri-ref-01/data/text_data_dict.json', 'utf8'));
console.log('=== category 138 (ours) : first 6 and last 6 ===');
const c138 = Object.entries(o['138'] || {});
console.log('count=' + c138.length);
for (const [k, v] of c138.slice(0, 6)) console.log(`  ${k}: ${v.replace(/\n/g, '\\n').slice(0, 50)}`);
console.log('  ...');
for (const [k, v] of c138.slice(-6)) console.log(`  ${k}: ${v.replace(/\n/g, '\\n').slice(0, 50)}`);

const db = new DatabaseSync('E:/HJM/_ref01/matsuri-ref-01/data/master.mdb', { readOnly: true });

console.log('\n=== need_skill_point distribution ===');
console.log(JSON.stringify(db.prepare(
  'SELECT need_skill_point AS pt, COUNT(*) AS n FROM single_mode_skill_need_point GROUP BY need_skill_point ORDER BY need_skill_point'
).all()));

console.log('\n=== join: need_point x skill_data (sample) ===');
const rows = db.prepare(`
  SELECT p.id, p.need_skill_point AS pt, p.status_type AS st, p.status_value AS sv, p.solvable_type AS solv,
         d.rarity, d.grade_value, d.skill_category AS cat, d.exp_type, d.potential_per_default AS ppd,
         d.disable_singlemode AS off
  FROM single_mode_skill_need_point p LEFT JOIN skill_data d ON d.id = p.id
  ORDER BY p.need_skill_point DESC LIMIT 12`).all();
for (const r of rows) console.log('  ' + JSON.stringify(r));

console.log('\n=== how many need_point ids are NOT in skill_data ===');
console.log(JSON.stringify(db.prepare(
  'SELECT COUNT(*) AS missing FROM single_mode_skill_need_point p WHERE p.id NOT IN (SELECT id FROM skill_data)').get()));

console.log('\n=== skill_level_value full (331 rows compressed) ===');
const lv = db.prepare('SELECT ability_type, level, float_ability_value_coef AS coef FROM skill_level_value ORDER BY ability_type, level').all();
const byType = {};
for (const r of lv) (byType[r.ability_type] ||= []).push(`${r.level}:${r.coef}`);
for (const [t, arr] of Object.entries(byType)) console.log(`  ability_type ${t} -> ${arr.join(' ')}`);

console.log('\n=== skill_exp all ===');
console.log(JSON.stringify(db.prepare('SELECT type, level, scale FROM skill_exp ORDER BY type, level').all()));
