#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#83 第 1 步：把「可点的免费进化」全集算出来（纯表侧，不读计数器）。

三族来源（都已在 `docs/技能到手分解_20261106.md` §6/§6.6 用一手证实存在）：
  A 剧本链   `skill_upgrade_speciality(scenario_id=15)` → 产物（`upgrade_type=2`）
  B 继承链   `skill_upgrade_succession_skill`          → 产物（911xxx）
  C 卡进化   `skill_upgrade_description(card_id,rank)` → 产物（`{育成卡号}111/211/221`，rank＝凸 3/5）

**基座靠什么认**：表侧没有任何一列写 `base→product`（`unique_skill_id_*` 全 0，
`available_skill_set`/`skill_exp`/`card_talent_hint_upgrade` 都匹配不到产物 id）。
能用的只有语义列：产物与被换掉的基座 **`precondition_1` 与 `condition_1` 完全相同**
（实测 `415011` 与基座 `202551` 的 `condition_1` 都是 `distance_rate_after_random==50`；
`91101411` 与 `901411` 两列全同；`106001211` 的 `running_style==3&phase_random==2`
对上基座 `201441` 的同名前缀）⇒ 用「同条件串 ＋ grade 更低」认基座，Δ＝产物−基座。
这条是**表侧推导**，不是拟合，也不引入新常数。

只读 SQLite（mode=ro）。不写 `data/`、不动引擎、不动常数。
"""
from __future__ import annotations

import os
import sqlite3
import sys

MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
# 我们报文里真出现过的育成卡（= 用户实际在用的那批）
OUR_CARDS = [106001, 106301, 114101, 100402, 111401, 106702]


def main() -> int:
    con = sqlite3.connect("file:%s?mode=ro" % MDB.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    sk = {r["id"]: r for r in con.execute(
        "SELECT id,grade_value,rarity,disable_singlemode,precondition_1,condition_1 "
        "FROM skill_data")}
    price = {r["id"]: r["need_skill_point"] for r in con.execute(
        "SELECT id,need_skill_point FROM single_mode_skill_need_point")}
    cond = {r["id"]: r for r in con.execute("SELECT * FROM skill_upgrade_condition")}

    # 按「条件串」把技能分组，用于认基座
    by_sig = {}
    for sid, r in sk.items():
        sig = (str(r["precondition_1"] or ""), str(r["condition_1"] or ""))
        if sid not in price:            # 只有「无价目」的产物需要找基座
            by_sig.setdefault(sig, []).append(sid)

    chains = []                          # (来源, 产物, 基座候选, condition_id 列表)
    for r in con.execute("SELECT id,base_skill_id,skill_id FROM skill_upgrade_speciality "
                         "WHERE scenario_id=15"):
        chains.append(("剧本A", int(r["skill_id"]), int(r["base_skill_id"])))
    for r in con.execute("SELECT base_skill_id,skill_id FROM skill_upgrade_succession_skill"):
        chains.append(("继承B", int(r["skill_id"]), int(r["base_skill_id"])))
    for cid in OUR_CARDS:
        for r in con.execute("SELECT id,rank,skill_id FROM skill_upgrade_description "
                             "WHERE card_id=? ORDER BY rank", (cid,)):
            chains.append(("卡C%d" % r["rank"], int(r["skill_id"]), None))

    print("== 可点免费进化全集（产物 id／grade／Δ／基座怎么认）==")
    print("%-6s %-11s %6s %-11s %6s %7s %s"
          % ("来源", "产物", "grade", "基座", "基座g", "Δ", "价目(产物)"))
    tot_by_src = {}
    for src, prod, base in chains:
        p = sk.get(prod)
        if p is None:
            continue
        pg = int(p["grade_value"] or 0)
        if base is None:                 # 卡进化：表里没写基座，用条件串认
            sig = (str(p["precondition_1"] or ""), str(p["condition_1"] or ""))
            # 卡进化的基座条件串是产物的**前缀超集**（实测 201441 的 condition_1
            # = `running_style==3&phase_random==2&order_rate...`，产物 106001211 只有前两段）
            cands = [s for s, r in sk.items() if s != prod and s in price
                     and (str(r["precondition_1"] or ""), str(r["condition_1"] or ""))[0] == sig[0]
                     and str(r["condition_1"] or "").startswith(sig[1]) and sig[1]
                     and int(r["grade_value"] or 0) < pg]
            base = max(cands, key=lambda s: int(sk[s]["grade_value"] or 0)) if cands else None
        bg = int(sk[base]["grade_value"] or 0) if base in sk else None
        d = (pg - bg) if bg is not None else None
        if d is not None:
            tot_by_src[src] = tot_by_src.get(src, 0) + d
        print("%-6s %-11s %6s %-11s %6s %7s %s"
              % (src, prod, pg, base if base else "未认出", bg if bg is not None else "-",
                 "+%s" % d if d is not None else "-",
                 "无价目 ⇒ 不吃 PT" if prod not in price else "有价目 %s" % price[prod]))

    print("\n== 按来源合计的可达 Δ（单位＝Σgrade，K≈1.0705 折分只当尺子）==")
    for src, v in sorted(tot_by_src.items()):
        print("  %-8s Δ%+5s Σgrade ≈ %+5.0f 分" % (src, v, 1.0705 * v))

    print("\n== 这几条链的条件行（决定它到底数什么，仍未解）==")
    for cid in sorted(cond):
        r = cond[cid]
        if str(cid).startswith("415"):
            print("   cond %-11s type=%s num=%s sub=%s timing=%s count=%s  → 产物 %s"
                  % (cid, r["upgrade_type"], r["num"], r["sub_num"], r["timing_type"],
                     r["count_type"], str(cid)[:6]))
    print("\n[边界] 卡进化的基座是**用条件串推出来的**（表侧无 base→product 列），"
          "认不出或认错的行会标「未认出」；Δ 是表侧差值，不是实测收益——"
          "实测收益请看 docs §6.6 的配对 diff（+310/+375/+495）。")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
