// #92 用：把 single_mode_15_act_effect 的 scen15 按 (演目 Lv × effect_type) 摊成矩阵，
// 并把「201 那条列折算成每级阶梯」直接写出来，和 tools/festa_sp_table.py 的实测阶梯并排比。
// 只读。用法：node tools/act_effect_ladder.js [scenario_id]
const { DatabaseSync } = require("node:sqlite");
const DB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb";
const scen = Number(process.argv[2] || 15);
const db = new DatabaseSync(DB, { readOnly: true });

const has = db.prepare("select count(*) c from pragma_table_info('single_mode_15_act_effect') where name='scenario_id'").get().c;
const rows = has
  ? db.prepare("select * from single_mode_15_act_effect where scenario_id=?").all(scen)
  : db.prepare("select * from single_mode_15_act_effect").all();
console.log(`single_mode_15_act_effect：${rows.length} 行（scenario_id 列存在＝${!!has}）`);

const LVS = [...new Set(rows.map(r => r.level))].sort((a, b) => a - b);
const TYPES = [...new Set(rows.map(r => r.effect_type))].sort((a, b) => a - b);
const cell = {};
for (const r of rows) cell[`${r.level}|${r.effect_type}|${r.condition_type}|${r.condition_value}`] = r;

console.log("\n每行的四列数值（ev1..ev4）按 (Lv, effect_type) 摊开：");
console.log("Lv  type cond           ev1   ev2   ev3   ev4");
for (const lv of LVS) {
  for (const t of TYPES) {
    for (const key of Object.keys(cell)) {
      const [l, tt, ct, cv] = key.split("|").map(Number);
      if (l === lv && tt === t) {
        const r = cell[key];
        console.log(`${String(lv).padStart(2)}  ${String(t).padStart(4)}  ct=${ct} cv=${cv}   ` +
          `${String(r.effect_value_1).padStart(4)} ${String(r.effect_value_2).padStart(4)} ` +
          `${String(r.effect_value_3).padStart(4)} ${String(r.effect_value_4).padStart(4)}`);
      }
    }
  }
}

// 201（condition_type=1, value=5）那条：ev4 当作百分比加成 ⇒ 折算成每级阶梯
const g = (lv) => {
  for (const key of Object.keys(cell)) {
    const [l, t, ct, cv] = key.split("|").map(Number);
    if (l === lv && t === 201 && ct === 1 && cv === 5) return cell[key];
  }
  return null;
};
console.log("\n=== effect_type 201 的 ev4 序列 ⇒ 若 SP 腿 ∝ (1 + ev4/100)，每级阶梯是：");
let prev = null;
for (const lv of LVS) {
  const r = g(lv);
  if (!r) continue;
  const mult = 1 + r.effect_value_4 / 100;
  console.log(`  L${lv}: ev4=${String(r.effect_value_4).padStart(3)}  ev1=${r.effect_value_1}  ⇒ ×${mult.toFixed(2)}` +
    (prev ? `   阶梯 ${(mult / prev).toFixed(3)}` : ""));
  prev = mult;
}
db.close();
