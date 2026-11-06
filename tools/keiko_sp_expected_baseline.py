#!/usr/bin/env python3
"""#92 的**一手基线**算式：把「SP 腿 ÷ 收益腿」这个比值该随演目 Lv 涨多少算出来，
再去比真机实测，从而判断「超出表侧的那一截」到底存不存在、有多大。

为什么必须这样算（而不是拿引擎读数当基线）：稽古两条腿**不是同一条乘区**——
`mod.rs:1360` 收益腿吃 `main_mult=(1+main_pct/100)×effect_mult`，
而 `:1362` SP 腿只吃 `pt_mult=effect_mult`（教诲 21 明写「スキルPtは対象外」）。
所以真机/引擎的比值里天生带着一个 `1/(1+main_pct/100)`，
`effect_pct` 那一路在分子分母**约掉**，剩下的期望成长是：

    每级该涨 = [(1+201_{l+1})/(1+201_l)] × [(1+13_l)/(1+13_{l+1})]

两个数列都直接从一手表读（`scenario_matsuri.json`，与 master.mdb 已逐行对过：153/153 全等）。
成员数（`main_pct × members`）报文不可见 ⇒ 给 k=1 与 k=2 两种口径，当作区间的来源之一。

    python tools/keiko_sp_expected_baseline.py
"""
import argparse
import collections
import json
import statistics as st

AX = ["速度", "耐力", "力量", "根性", "智力"]


def load_tables(path):
    d = json.load(open(path, encoding="utf-8"))
    p = {e["level"]: e["sp_practice_pct"] for e in d["act_level_effects"]}
    m13 = collections.defaultdict(float)
    m203 = collections.defaultdict(float)
    for r in d.get("advice_effects") or []:
        if r.get("effect_type") == 13:
            m13[(r["advice_id"], r["level"])] += r.get("value", 0)
        elif r.get("effect_type") == 203:
            m203[(r["advice_id"], r["level"])] += r.get("value", 0)
    return p, m13, m203


def expected_step(p, m13, adv, l1, l2, k=1):
    """比值在 l1→l2 之间该涨多少（每级）。"""
    a = (1 + p[l2] / 100) / (1 + p[l1] / 100)
    v1 = 1 + k * m13.get((adv, l1), 0) / 100
    v2 = 1 + k * m13.get((adv, l2), 0) / 100
    return (a * v1 / v2) ** (1 / (l2 - l1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hands", default="out/festa_sp_hands.jsonl")
    ap.add_argument("--tables", default="data/scenario_matsuri.json")
    ap.add_argument("--min-n", type=int, default=8, help="一格至少几手才用")
    a = ap.parse_args()

    p, m13, m203 = load_tables(a.tables)
    hs = [json.loads(l) for l in open(a.hands, encoding="utf-8") if l.strip()]
    ok = [h for h in hs if h.get("tt30") and h.get("tt_axis") and h.get("act_lv")
          and h.get("axis_val") is not None and h["axis_val"] + h["tt_axis"] <= 1200]
    cell = collections.defaultdict(list)
    for h in ok:
        cell[(h["advice"], h["axis_name"], h["act_lv"])].append(h["tt30"] / h["tt_axis"])

    for k in (1, 2):
        rows = []
        for adv, ax in sorted({k[:2] for k in cell}):
            lvls = sorted(lv for (a2, x2, lv), v in cell.items() if (a2, x2) == (adv, ax) and len(v) >= a.min_n)
            d = {(lv2): cell[(adv, ax, lv2)] for lv2 in lvls}
            for l1 in lvls:
                for l2 in lvls:
                    if l2 <= l1 or l2 - l1 > 3 or len(d[l1]) < a.min_n or len(d[l2]) < a.min_n:
                        continue
                    obs = (st.median(d[l2]) / st.median(d[l1])) ** (1 / (l2 - l1))
                    exp = expected_step(p, m13, adv, l1, l2, k)
                    if exp > 0:
                        rows.append((adv, ax, l1, l2, obs, exp, obs / exp))
        if not rows:
            print(f"成员数 k={k}: 没有可比对"); continue
        rr = sorted(x[6] for x in rows)
        print(f"\n=== 成员数口径 k={k}：可比对 {len(rows)} 组（同教诲、同轴、分母未砍）")
        print(f"    实测 ÷ 一手期望 = 中位 {rr[len(rr)//2]:.3f}   p25 {rr[len(rr)//4]:.3f}   "
              f"p75 {rr[3*len(rr)//4]:.3f}   最小 {rr[0]:.3f} 最大 {rr[-1]:.3f}")
        print(f"    比值涨 > 期望 的对数：{sum(1 for x in rr if x > 1)}/{len(rr)}"
              f"；落在 [0.9,1.1]（即无缺口）的：{sum(1 for x in rr if 0.9 <= x <= 1.1)}/{len(rr)}")
        byadv = collections.defaultdict(list)
        for adv, ax, l1, l2, o, e, r in rows:
            byadv[adv].append(r)
        top = sorted(byadv.items(), key=lambda x: -len(x[1]))
        parts = []
        for adv, v in top:
            if len(v) < 5:
                continue
            vv = sorted(v)
            parts.append(f"{adv}={vv[len(vv)//2]:.2f}(n{len(vv)})")
        line = "    按教诲：" + "  ".join(parts)
        print(line)
    print("""
判据：
 · 「实测 ÷ 期望」≈1.00 ⇒ 比值成长被一手算式解释完，#92 到此结案（缺口不存在）。
 · 明显 >1.00 ⇒ 超出部分每级就是这个倍数，且它是**在钉住教诲 id、同轴、分母未砍、
   并按一手算式扣掉 201 与主轴13 之后**剩下的——到这一步才有资格谈去找载体或申请经验标定。""")
    print(f"\n用到的一手数列：201 sp_practice_pct {[(l, p[l]) for l in sorted(p)]}")
    print(f"              type13 主轴% 行数 {len(m13)}（按 (教诲,Lv)）")


if __name__ == "__main__":
    main()
