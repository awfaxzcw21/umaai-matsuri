// #92：系统找「等比阶梯」载体——扫 master.mdb 里所有数值列，
// 把每个 (分组键, 数值列) 的 2..10 个取值排成序列，看相邻比值是否稳定在 [1.35, 1.75]。
// 一手表里若真有 ~1.55/级 的那一列，就该在这里现形；找不到就是负结果（负结果也要登记）。
// 只读。用法：node tools/find_geometric_columns.js [lo] [hi]
const { DatabaseSync } = require("node:sqlite");
const DB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb";
const LO = Number(process.argv[2] || 1.35);
const HI = Number(process.argv[3] || 1.75);
const db = new DatabaseSync(DB, { readOnly: true });
const tables = db.prepare("select name from sqlite_master where type='table'").all().map(r => r.name);
const hits = [];
let scanned = 0;
for (const tb of tables) {
  let cols;
  try { cols = db.prepare(`pragma table_info(${tb})`).all(); } catch { continue; }
  const numCols = cols.filter(c => /int|real|numeric/i.test(c.type || "") && !/^(id|rowid)$/i.test(c.name));
  if (numCols.length < 2) continue;
  // 用第一个非数值列做分组键（通常是 scenario_id / level / type 那一族）
  let rows;
  try { rows = db.prepare(`select * from ${tb} limit 20000`).all(); } catch { continue; }
  if (rows.length < 3) continue;
  const keyCols = cols.map(c => c.name).filter(n => !numCols.some(x => x.name === n));
  const groups = new Map();
  for (const r of rows) {
    const kk = keyCols.slice(0, 3).map(k => String(r[k])).join("|");
    if (!groups.has(kk)) groups.set(kk, []);
    groups.get(kk).push(r);
  }
  for (const [kk, rs] of groups) {
    if (rs.length < 4 || rs.length > 12) continue;
    for (const c of numCols) {
      scanned++;
      const seq = rs.map(r => r[c.name]).filter(v => typeof v === "number" && v > 0);
      if (seq.length < 4) continue;
      const sorted = [...seq].sort((a, b) => a - b);
      const ratios = [];
      for (let i = 1; i < sorted.length; i++) ratios.push(sorted[i] / sorted[i - 1]);
      const uniq = new Set(sorted);
      if (uniq.size < 4) continue;              // 平台列（全同值）跳过
      const ok = ratios.every(x => x >= LO && x <= HI);
      const last = sorted[sorted.length - 1] / sorted[0];
      if (ok && last >= 2.5) {
        hits.push({ table: tb, col: c.name, group: kk, seq: sorted, ratio: ratios });
      }
    }
  }
}
console.log(`扫了 ${tables.length} 张表、${scanned} 个数值列分组；命中 ${hits.length} 条比值稳定在 [${LO}, ${HI}] 的序列`);
for (const h of hits.slice(0, 25)) {
  const m = h.ratio.reduce((a, b) => a + b, 0) / h.ratio.length;
  console.log(`  ${h.table}.${h.col}  组[${h.group}]  序列 ${h.seq.join(", ")}  平均比 ${m.toFixed(3)}`);
}
if (!hits.length) console.log("  ⇒ 负结果：一手库里没有一条列能直接给出这个阶梯。");
db.close();
