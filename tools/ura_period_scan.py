#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URA 收尾期窗口的表侧一手核对（#41/#52 的 U41 项）。

读 master.mdb 的 `single_mode_turn`，把 period / training_set_id / rest_type /
outing_set_id / race_entry_type 六个字段的回合分布打全，用来判两件事：
  1. `period = 3`（各剧本通用的「收尾期」标记）落在哪几个回合，和 memo 的面板 68–77 对不对得上；
  2. 表侧回合号 ↔ 引擎回合号（引擎 0 基）的偏移，用合宿窗口当尺子独立确认。

用法：python tools/ura_period_scan.py [master.mdb 路径] [--out out/_ura_period_scan_20261104.txt]
只读数据库，不写引擎、不改配置。
"""
import argparse
import io
import os
import sqlite3
import sys

DEFAULT_DB = r"E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
SCEN = 15


def rows(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchall()


def span(cur, col, where, args=()):
    """某个字段取值 -> (首回合, 末回合, 行数) 分布。"""
    sql = (f"select {col}, min(turn), max(turn), count(*) from single_mode_turn "
           f"where {where} group by 1 order by 1")
    return rows(cur, sql, args)


def report(db):
    out = io.StringIO()
    con = sqlite3.connect(db)
    cur = con.cursor()
    p = lambda *a: print(*a, file=out)

    p(f"# master.mdb = {db}")
    p(f"# single_mode_turn 总行数 = {cur.execute('select count(*) from single_mode_turn').fetchone()[0]}")
    p(f"# 回合号基数：表侧 turn 从 "
      f"{cur.execute('select min(turn) from single_mode_turn').fetchone()[0]} 起，"
      f"scenario {SCEN} 末回合 "
      f"{cur.execute('select max(turn) from single_mode_turn where turn_set_id=?', (SCEN,)).fetchone()[0]}"
      f"（引擎 0 基 ⇒ 引擎回合 = 表侧 − 1）")

    p("\n## 1. 剧本 15：period 值域")
    p("  period  首回合  末回合  行数")
    for v, a, b, n in span(cur, "period", "turn_set_id=?", (SCEN,)):
        p(f"  {v:>6}  {a:>6}  {b:>6}  {n:>4}")

    p("\n## 2. 跨剧本对照：period = 3 的窗口（判它是不是通用收尾期标记）")
    p("  turn_set_id  首回合  末回合  行数")
    for v, a, b, n in rows(cur, "select turn_set_id, min(turn), max(turn), count(*) "
                               "from single_mode_turn where period=3 group by 1 order by 1"):
        p(f"  {v:>10}  {a:>6}  {b:>6}  {n:>4}")
    n = rows(cur, "select min(turn), max(turn), count(distinct turn_set_id) "
                  "from single_mode_turn where period=3")
    p(f"  ⇒ 共 {n[0][2]} 个 turn_set，窗口全部为 turn {n[0][0]}–{n[0][1]}"
      f"（{'一致' if len({(a, b) for _, a, b in rows(cur, 'select turn_set_id, min(turn), max(turn) from single_mode_turn where period=3 group by 1')}) == 1 else '不一致'}）")

    p("\n## 3. 剧本 15：period × rest_type × outing_set_id × training_set_id 组合")
    p("  period  rest  outing  tr_set  行数  首回合  末回合")
    for r in rows(cur, "select period, rest_type, outing_set_id, training_set_id, count(*), min(turn), max(turn) "
                       "from single_mode_turn where turn_set_id=? "
                       "group by 1,2,3,4 order by 6", (SCEN,)):
        p("  %6s  %4s  %6s  %6s  %4s  %6s  %6s" % r)

    p("\n## 4. 尺子：training_set_id = 6（合宿）在表侧覆盖哪些回合")
    for v, a, b, n in rows(cur, "select training_set_id, min(turn), max(turn), count(*) "
                               "from single_mode_turn where turn_set_id=? group by 1 order by 1", (SCEN,)):
        p(f"  tr_set={v}  turns {a}..{b}  n={n}")
    camps = [t for (t,) in rows(cur, "select turn from single_mode_turn "
                                    "where turn_set_id=? and training_set_id=6 order by turn", (SCEN,))]
    grp, run = [], []
    for t in camps:
        if run and t == run[-1] + 1:
            run.append(t)
        else:
            if run:
                grp.append(run)
            run = [t]
    if run:
        grp.append(run)
    p("  ⇒ 表侧合宿窗口：" + " / ".join(f"{g[0]}–{g[-1]}" for g in grp)
      + "（引擎口径减 1 ⇒ 应为 12–15 / 36–39 / 60–63，与 memo §2.4 对齐则基数确认）")

    p("\n## 5. race_entry_type：哪些回合禁止报名（判收尾期比赛日）")
    zero = [t for (t,) in rows(cur, "select turn from single_mode_turn "
                                    "where turn_set_id=? and race_entry_type=0 order by turn", (SCEN,))]
    p(f"  race_entry_type=0 的回合：{zero}")
    p(f"  ⇒ 收尾期 73–78 内可报名的回合：{[t for t in range(73, 79) if t not in zero]}")

    p("\n## 6. 逐回合原样（剧本 15，turn 60–78）")
    p("  turn  year month half period tr_set outing race_entry rest  unique_cmd  ⇒引擎turn")
    for r in rows(cur, "select turn, year, month, half, period, training_set_id, outing_set_id,"
                       " race_entry_type, rest_type, unique_command from single_mode_turn "
                       "where turn_set_id=? and turn between 60 and 78 order by turn", (SCEN,)):
        p("  %4s  %4s %5s %4s %6s %6s %6s %10s %4s %10s  %8s" % (*r, r[0] - 1))

    p("\n## 7. 裁决要点")
    p("  - 表侧 period=3 的窗口是「所有 turn_set 通用」还是本剧本特有 ⇒ 见 §2 的一致性。")
    p("  - 引擎现役 URA 窗口 = 引擎 67..=76（= 表侧 68–77）；表侧 period=3 = 表侧 73–78"
      "（= 引擎 72..=77 ⇒ 面板 74–78）。")
    p("  - 第 1 年合宿（表侧 13–16）是 period=0 / rest=1 / outing=1，第 2、3 年才是 period=2 /"
      " rest=0 / outing=2")
    p("    ⇒ 表侧独立佐证 memo U33（第 1 年合宿可普通 / 友人外出、正常休息；后两年不行）。")
    p("  - 本脚本只登记表侧形状，不改 `crates/matsuri-core/src/game/mod.rs` 的窗口常数；"
      "改窗口要配对 CRN 三分量 + 95% t 区间才谈转正。")
    con.close()
    return out.getvalue()


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("db", nargs="?", default=DEFAULT_DB)
    ap.add_argument("--out", default=os.path.join("out", "_ura_period_scan_20261104.txt"))
    ap.add_argument("--stdout", action="store_true", help="只打印，不落盘")
    a = ap.parse_args()
    txt = report(a.db)
    if not a.stdout:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        print(f"已写 {a.out}", file=sys.stderr)
    print(txt)


if __name__ == "__main__":
    main()
