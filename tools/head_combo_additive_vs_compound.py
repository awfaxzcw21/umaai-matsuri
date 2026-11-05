#!/usr/bin/env python3
"""#78 关键判据：真机的多人头收益到底是「相加」还是「连乘」，超加性有多少。

为什么要它：引擎侧 0/1/2/3 闪彩人头的 PT 倍率是 1.0 / 2.6 / 6.8 / 19（`decompose_head_compounding_terms`），
真机是 1.0 / 2.0 / 3.7 / 5.25。两边都超过「纯相加」，但超出的量差一个数量级。
**光比这两条曲线定不了改哪一处**，因为里面混着两个独立问题：

  A. 单卡倍率 m1 本身对不对（引擎 2.6 vs 真机 2.0 ⇒ 可能单卡效果就偏大）
  B. 多卡的合成方式对不对（纯相加的预测是 1+n(m1−1)，真机只比它高 ~23%，引擎高 375%）

本脚本把 B 单独量出来：同一 Lv、同一「非卡组人头数」的分层里，
用 0 人头的 PT 当基、1 人头的 PT 当 m1，外推「相加」和「连乘」两条预测线，
和 2/3 人头的实测中位对表。

只读报文，不写 `data/`，不改引擎。

    python tools/head_combo_additive_vs_compound.py
"""
import collections
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import train_leg_preview as tlp  # noqa: E402

BANDS = (30, 79)


def main():
    # (kind, camp, Lv, heads_other) -> {闪彩人头数: [PT]}
    buckets = collections.defaultdict(lambda: collections.defaultdict(list))
    for f, data in tlp.decode_all():
        cia = (data.get("home_info") or {}).get("command_info_array")
        if not cia:
            continue
        ci = data.get("chara_info") or {}
        turn = ci.get("turn")
        if not isinstance(turn, int) or not (BANDS[0] <= turn <= BANDS[1]):
            continue
        bonds = {}
        for e in (ci.get("evaluation_info_array") or []):
            if isinstance(e, dict) and isinstance(e.get("training_partner_id"), int):
                bonds[e["training_partner_id"]] = int(e.get("evaluation") or 0)
        mot = ci.get("motivation")
        for c in cia:
            cid = c.get("command_id")
            if cid not in tlp.PLAIN or not c.get("params_inc_dec_info_array"):
                continue
            pt, five, vit = tlp.preview_of(c)
            if pt is None:
                continue
            deck = [p for p in (c.get("training_partner_array") or [])
                    if isinstance(p, int) and p < 100]
            shine = sum(1 for p in deck if bonds.get(p, 0) >= 80)
            other = len([p for p in (c.get("training_partner_array") or [])
                         if isinstance(p, int) and p >= 100])
            lv = int(c.get("level") or 0)
            if lv < 1 or lv > 5:
                continue
            # 干劲分一层：它自己就是一个乘区，不分层会把 A/B 两个问题搅在一起
            key = (lv, other, mot if isinstance(mot, int) else -1)
            buckets[key][shine].append(pt)

    print("分层 = (训练Lv, 非卡组人头数, 干劲)，层内按闪彩人头数取 PT 中位")
    print("%-22s %8s %8s %8s %8s | %8s %8s %8s"
          % ("层(Lv,其它人头,干劲)", "n0", "n1", "n2", "n3",
             "m1实测", "相加预测", "连乘预测"))
    rows = []
    for key in sorted(buckets):
        d = buckets[key]
        def med(n):
            v = d.get(n) or []
            return statistics.median(v) if len(v) >= 4 else None
        m0, m1v, m2v, m3v = med(0), med(1), med(2), med(3)
        if not m0 or m1v is None:
            continue
        mm1 = m1v / m0
        add = m0 * (1 + 2 * (mm1 - 1)) if m2v is not None else None
        mul = m0 * (mm1 ** 2) if m2v is not None else None
        print("%-22s %8.1f %8s %8s %8s | %8.2f %8s %8s"
              % (str(key), m0,
                 "%.1f" % m1v if m1v else "-",
                 "%.1f" % m2v if m2v else "-",
                 "%.1f" % m3v if m3v else "-",
                 mm1,
                 "%.1f→%.2f×" % (add, add / m0) if add else "-",
                 "%.1f→%.2f×" % (mul, mul / m0) if mul else "-"))
        if m2v is not None:
            rows.append((m2v / m0, add / m0, mul / m0, mm1))

    if rows:
        obs = statistics.fmean(r[0] for r in rows)
        ad = statistics.fmean(r[1] for r in rows)
        cm = statistics.fmean(r[2] for r in rows)
        print("\n== 汇总（%d 个可判层，2 闪彩人头处）==" % len(rows))
        print("  实测倍率均值   %.2f×" % obs)
        print("  纯相加预测     %.2f×   ⇒ 真机超加性 %+d%%" % (ad, round(100 * (obs / ad - 1))))
        print("  纯连乘预测     %.2f×   ⇒ 实测比连乘低 %d%%" % (cm, round(100 * (1 - obs / cm))))
        print("  单卡倍率 m1 均值 %.2f×（引擎同口径 2.6×）"
              % statistics.fmean(r[3] for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
