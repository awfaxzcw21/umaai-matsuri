const { DatabaseSync } = require('node:sqlite');
const db = new DatabaseSync('E:/HJM/_ref01/matsuri-ref-01/data/master.mdb', { readOnly: true });

const tables = ['single_mode_skill_need_point', 'skill_data', 'skill_level_value', 'skill_exp',
  'skill_set', 'available_skill_set', 'trainer_skill', 'skill_upgrade_condition',
  'skill_upgrade_description', 'single_mode_14_finals_gain_skill'];

for (const t of tables) {
  const cols = db.prepare(`PRAGMA table_info(${t})`).all();
  const n = db.prepare(`SELECT COUNT(*) AS c FROM ${t}`).get().c;
  console.log(`\n===== ${t}  rows=${n}`);
  console.log('  cols: ' + cols.map(c => `${c.name}:${c.type}`).join(', '));
  const rows = db.prepare(`SELECT * FROM ${t} LIMIT 3`).all();
  for (const r of rows) console.log('  ' + JSON.stringify(r).slice(0, 320));
}
