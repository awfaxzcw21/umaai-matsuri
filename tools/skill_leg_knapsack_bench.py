#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能腿有没有「分配余量」——同预算下，背包最优 vs 370 匹一手 LG15+ 的真实清单。

一手输入：`out/_lf_ref_highscore.json`（剧本15、`rank_score≥85,600` 的终局快照）。
表侧输入：`master.mdb` 的 `skill_data.grade_value` ＋ `single_mode_skill_need_point.need_skill_point`。

三条口径先钉住（都是本篇实测，不是假设）：
1. **只有 `disable_singlemode=0` 能吃 PT 直购**（451 条，全是 rarity=1）。同 `group_id` 的
   另外三条（进化体 / 负分回退条 / rarity2 上位）表侧带价但 `disable=1` ⇒ 只能靠进化/提示拿，
   所以背包只能也应该只在可购池里动。
2. **一匹马不会同组拿两条**（370/370 实测 0 组冲突）⇒ 可购池内天然互斥，退化成普通 0/1 背包。
3. 预算单位用**表价**（`need_skill_point`），不用 `total_skill_pt`：后者 med 10,251 和
   「全清单表价 med 13,690」不同口径（§7.6 已证表价≠实扣），混用会造出假余量。

判据分三层，从严到宽：
  T1 **演示过的余量**：同预算分箱内，真实马之间最好 vs 中位 ⇒ 这个差一定可达（有人真拿到了）。
  T2 **可购池背包最优** vs 真实 ⇒ 分配的理论天花板。
  T3 天花板减去 T1 ⇒ 「要靠机制/卡池才够得着」的部分，不能当承诺。

只读 SQLite（mode=ro）+ 只读 JSON。不写 `data/`、不改引擎、不动常数、不进估值层。
"""
import json
import os
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(ROOT, "out", "_lf_ref_highscore.json")
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
LG15_LINE = 85600
STEP = 10          # 表价全是 10 的倍数 ⇒ 背包按 10 分格无损
BUCKET = 500       # 预算分箱宽度


def med(xs):
    return statistics.median(xs) if xs else float("nan")


def q(xs, p):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[min(len(s) - 1, int(p * len(s)))]


def load():
    con = sqlite3.connect("file:%s?mode=ro" % MDB.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    sk = {r["id"]: r for r in con.execute(
        "SELECT id,grade_value,rarity,disable_singlemode,group_id FROM skill_data")}
    price = {r["id"]: r["need_skill_point"] for r in con.execute(
        "SELECT id,need_skill_point FROM single_mode_skill_need_point")}
    con.close()
    return sk, price


def greedy_ratio(items, budget, max_n):
    """面板现在的口径：按 分/价 降序取，装得下才取，最多 max_n 条。"""
    got, left, n = 0, int(budget), 0
    for g, p in sorted(items, key=lambda x: -(x[0] / x[1])):
        if n >= max_n:
            break
        if p <= left:
            got += g
            left -= p
            n += 1
    return got


def knapsack(items, budget):
    """0/1 背包（1-D 预算版）：给 T2 的双约束版当参照实现。"""
    b = max(0, int(budget) // STEP)
    dp = [0] * (b + 1)
    for g, p in items:
        w = max(1, int(p) // STEP)
        for c in range(b, w - 1, -1):
            if dp[c - w] + g > dp[c]:
                dp[c] = dp[c - w] + g
    return dp[b]


def main():
    sk, price = load()
    raw = json.load(open(CORPUS, encoding="utf-8"))
    games = [h for h in raw if h.get("scenario_id") == 15
             and (h.get("rank_score") or 0) >= LG15_LINE]
    if not games:
        print("corpus 里没有剧本15高分样本")
        return 1
    print("== 样本：%d 匹（剧本15，rank_score≥%s）==" % (len(games), f"{LG15_LINE:,}"))

    # 逐马拆腿
    hold = Counter()
    rec = []
    for h in games:
        r = {"score": h["rank_score"], "tsp": h.get("total_skill_pt") or 0,
             "b_g": 0, "b_p": 0, "b_n": 0, "h_g": 0, "h_n": 0, "u_g": 0, "u_n": 0}
        for s in h.get("skill_array") or []:
            sid = s["skill_id"]
            hold[sid] += 1
            row = sk.get(sid)
            if row is None:
                continue
            g = int(row["grade_value"] or 0)
            p = price.get(sid)
            if p is None:
                r["u_g"] += g
                r["u_n"] += 1
            elif int(row["disable_singlemode"]) == 0:
                r["b_g"] += g
                r["b_p"] += p
                r["b_n"] += 1
            else:
                r["h_g"] += g
                r["h_n"] += 1
        rec.append(r)

    tg = sum(x["b_g"] + x["h_g"] + x["u_g"] for x in rec)
    print("\n== 真实清单的三条腿（Σgrade 占比 / 条数中位）==")
    for key, lbl in (("b", "可直购 dis=0"), ("h", "带价但 dis=1（进化/上位）"),
                     ("u", "无价目条目（固有等）")):
        g = sum(x[key + "_g"] for x in rec)
        print("  %-22s %5.1f%%  %6.1f 条  Σgrade med %s"
              % (lbl, 100.0 * g / tg, med([x[key + "_n"] for x in rec]),
                 f"{med([x[key + '_g'] for x in rec]):,.0f}"))

    pool = [(int(sk[i]["grade_value"] or 0), p) for i, p in price.items()
            if i in sk and int(sk[i]["disable_singlemode"]) == 0]
    ratios = [g / p for g, p in pool]
    print("\n== 可购池：表侧 dis=0 共 %d 条 ==" % len(pool))
    print("  价 %d–%d ｜ grade %d–%d ｜ 分/价 p10 %.3f med %.3f p90 %.3f ⇒ 比值带宽 %.0f%%"
          % (min(p for _, p in pool), max(p for _, p in pool),
             min(g for g, _ in pool), max(g for g, _ in pool),
             q(ratios, .10), med(ratios), q(ratios, .90),
             100.0 * (q(ratios, .90) / q(ratios, .10) - 1.0)))
    obs = sum(1 for i, p in price.items()
              if i in sk and int(sk[i]["disable_singlemode"]) == 0 and hold.get(i))
    print("  其中在 370 匹真实清单里出现过：%d/%d（%.1f%%）"
          % (obs, len(pool), 100.0 * obs / len(pool)))

    # T1：同预算分箱内，真实 vs 真实
    print("\n== T1 演示过的余量（同表价分箱内，真实马之间 best − med）==")
    boxes = defaultdict(list)
    for x in rec:
        boxes[x["b_p"] // BUCKET].append(x["b_g"])
    print("  %-16s %5s %10s %10s %10s" % ("预算箱", "n", "medΣgrade", "maxΣgrade", "余量"))
    t1 = []
    for k in sorted(boxes):
        v = boxes[k]
        if len(v) < 15:
            continue
        t1.append(max(v) - med(v))
        print("  %-16s %5d %10s %10s %+10s" % (
            f"{k*BUCKET:,}–{(k+1)*BUCKET:,}", len(v), f"{med(v):,.0f}",
            f"{max(v):,.0f}", f"{max(v)-med(v):+,.0f}"))
    print("  ⇒ 同箱内可达余量 med %+.0f Σgrade（n=%d 箱）" % (med(t1), len(t1)))

    # T2：背包天花板（**精确预算 + 精确条数**双约束，和真实一匹马同口径）
    #      F[cap, k] = 用 ≤cap 表价、≤k 条能拿到的最大 Σgrade ⇒ 一次 DP 出全网格
    dups = sum(len(x.get("skill_array") or [])
               - len({s["skill_id"] for s in (x.get("skill_array") or [])})
               for x in games)
    max_cap = max(x["b_p"] for x in rec)
    max_k = max(x["b_n"] for x in rec)
    # 条数维开到「池子大小」= 等价于不设限，这样纯预算版可以拿来做自洽校验
    kfull = len(pool)
    F = np.zeros((max_cap // STEP + 1, kfull + 1), dtype=np.int64)
    for g, p in pool:
        w = max(1, int(p) // STEP)
        # 倒序更新由切片方向保证（只用上一轮 = 未含本件的值）
        F[w:, 1:] = np.maximum(F[w:, 1:], F[:-w, :-1] + g)
    # 仪器自检：双约束 numpy 版在「条数不设限」时必须等于纯预算版
    assert int(F[max_cap // STEP, kfull]) == knapsack(pool, max_cap), "背包实现不自洽"
    print("\n== T2 同预算**且同条数**的背包最优 vs 真实（可购池 %d 条；清单内重复 id %d 个）=="
          % (len(pool), dups))
    gains, dp_g, gre_g, real_g = [], [], [], []
    for x in rec:
        cap, k = x["b_p"] // STEP, x["b_n"]
        v = int(F[cap, k])
        gains.append(v - x["b_g"])
        dp_g.append(v)
        gre_g.append(greedy_ratio(pool, x["b_p"], k))
        real_g.append(x["b_g"])
    print("  真实 med %s ｜ 背包 med %s ｜ 贪心(分/价降序) med %s"
          % (f"{med(real_g):,.0f}", f"{med(dp_g):,.0f}", f"{med(gre_g):,.0f}"))
    print("  背包 − 真实 med %+.0f（p10 %+.0f / p90 %+.0f）｜ 严格优于真实 %d/%d"
          % (med(gains), q(gains, .10), q(gains, .90),
             sum(1 for v in gains if v > 0), len(rec)))
    print("  背包 − 贪心 med %+.0f ⇒ **这条就是「换规划器」能买到的增量**"
          % med([a - b for a, b in zip(dp_g, gre_g)]))
    print("  ⇒ T3 天花板减演示余量 med %+.0f（无真实样本背书，不能当承诺）"
          % (med(gains) - med(t1)))
    # T2 的解到底现不现实：背包一定优先吃「性价比最高那批」，看它们的真实持有率
    buy_items = [(int(sk[i]["grade_value"] or 0), price[i], i) for i in price
                 if i in sk and int(sk[i]["disable_singlemode"]) == 0]
    n_med = int(med([x["b_n"] for x in rec]))
    top = sorted(buy_items, key=lambda x: -(x[0] / x[1]))[:n_med]
    hr = [hold.get(i, 0) / len(games) for _, _, i in top]
    print("  ⚠ 背包靠的「性价比前 %d 条」在真实清单里的持有率：med %.1f%%、<10%% 的有 %d 条"
          % (n_med, 100 * med(hr), sum(1 for v in hr if v < 0.10)))
    print("    ⇒ T2 隐含「451 条随你点」，而商店只发你见过的 ⇒ 只当上界，不当目标。")

    # T4：**可达性敏感性**——把池子按「真实持有率 ≥ τ」收窄，看余量怎么塌。
    #      τ 越高越贴近「商店真会发给你」，但池子条数必须 > 该马的可购条数，否则背包装不满、
    #      负数是**不可行**不是「没余量」⇒ 那几档要标出来，不能拿去下结论。
    print("\n== T4 可达性敏感性（池=全局持有率≥τ 的可购条；预算/条数同该马真实值）==")
    print("  ⚠ 「背包 vs 真实」在不同 τ 之间**不可比**（真实清单里本来就有低于 τ 的稀有条）；")
    print("    可比的是同一池子里**背包 vs 贪心**——两者面对同一个商店，可达性偏差自己抵消，")
    print("    这一列才是「把面板的分/价排序换成 DP 规划器」真正的增量。")
    print("  %-6s %6s %10s %10s %10s %12s" % ("τ", "池条数", "真实med", "背包med", "vs真实", "vs贪心=增量"))
    for tau in (0.50, 0.25, 0.10, 0.05, 0.02, 0.0):
        p4 = [(g, p, i) for g, p, i in buy_items if hold.get(i, 0) / len(games) >= tau]
        if not p4:
            continue
        cap4 = max(x["b_p"] for x in rec)
        k4 = max(x["b_n"] for x in rec)
        F4 = np.zeros((cap4 // STEP + 1, k4 + 1), dtype=np.int64)
        for g, p, _ in p4:
            w = max(1, int(p) // STEP)
            F4[w:, 1:] = np.maximum(F4[w:, 1:], F4[:-w, :-1] + g)
        feasible = [x for x in rec if x["b_n"] <= len(p4)]
        dd = [int(F4[x["b_p"] // STEP, x["b_n"]]) - x["b_g"] for x in feasible]
        items4 = [(g, p) for g, p, _ in p4]
        gg = [int(F4[x["b_p"] // STEP, x["b_n"]])
              - greedy_ratio(items4, x["b_p"], x["b_n"]) for x in feasible]
        print("  %-6s %6d %10s %10s %+10s %+12s（n=%d）" % (
            ("%d%%" % (100 * tau)) if tau else "全部", len(p4),
            f"{med([x['b_g'] for x in feasible]):,.0f}",
            f"{med([x['b_g'] + d for x, d in zip(feasible, dd)]):,.0f}",
            med(dd), med(gg), len(feasible)))
    print("  读法：τ 高⇒池小⇒装不满真实条数（那几行不可比、已按 b_n≤池条数 过滤）；")
    print("        τ 低⇒等于假设「451 条随你点」，商店不是这么发的。")
    print("  ⇒ 两端之间没有一个 τ 是报文能证的（本 corpus 只有终局清单，没有商店发牌记录），")
    print("    所以技能腿**唯一能引用的余量是 T1 的 +701**（同预算下真实马 best−med，有人真拿到过）。")

    # 折算成评分量级（K 只当尺子，见 docs/ref/skill_grade_value_20261105.md §8.2 禁令）
    K = 1.0705
    print("\n== 折成分数量级（K≈%.4f 仅作尺子，**不写进代码**）==" % K)
    print("  T1 演示余量 ≈ %+.0f 分 ｜ T2 背包天花板 ≈ %+.0f 分" % (K * med(t1), K * med(gains)))
    print("  对照：总PT差 −1,818 × 2.46 ＝ %+.0f 分；五维和差 −1,255 那一腿另算"
          % (-1818 * 2.46))

    print("\n== 池子里性价比最高、真实持有率却低的 12 条 ==")
    rows = sorted(((g / p, g, p, hold.get(i, 0) / len(games), i)
                   for i, p in price.items()
                   if i in sk and int(sk[i]["disable_singlemode"]) == 0
                   for g in [int(sk[i]["grade_value"] or 0)]), reverse=True)
    print("  %-10s %6s %6s %8s %8s" % ("skill_id", "grade", "表价", "分/价", "持有率"))
    for rpv, g, p, rt, sid in rows[:12]:
        print("  %-10s %6d %6d %8.3f %7.1f%%" % (sid, g, p, rpv, 100 * rt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
