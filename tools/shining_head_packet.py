#!/usr/bin/env python3
"""#78 判据：真机板上「羁绊≥80 的人头数」对 PT 的斜率，和引擎强制全闪彩的曲线对表。

为什么要它：#40 把 PT 按 `(训练Lv, 卡组人头)` 分桶，得出「引擎斜率只有真机 1/2.5～1/3」，
#41 据此判「估值没给人头定价」。但引擎的 `is_shining_at` 要**两个条件**（得意==本格 ∧ 羁绊≥80），
不闪彩的卡 `youqing=0`——所以「人头数」不是吃友情乘区的那份人头。
壳内单测（`_wt_head` 分支 `fix/head-slope`）把 n 张卡**全部**设成本类型+羁绊100 后，
引擎 Lv2 的 PT 曲线是 **7 / 17 / 43 / 117**（0..3 头）——比真机同桶中位 8/15/29/48 **更高**。
两边不能直接比，因为真机那一桶里不是全闪彩。本文把真机侧也按「闪彩人头」切开：

    b80 = 该命令 `training_partner_array` 里 <100 且 `evaluation_info_array` 给的
          evaluation ≥ 80 的个数

判读（写死，不许事后改）：
  · 若真机 PT 随 b80 的斜率 ≈ 引擎的 7/17/43/117 ⇒ **公式对，缺的是引擎盘面上 b80 的人数**
    ⇒ 活归 羁绊成长/站位类型匹配，不归人头系数。
  · 若真机随 b80 明显更平 ⇒ 引擎**超价**闪彩人头，#41 那句判读要整体改向。

只读报文，不写 `data/`，不改引擎。复用 #40 的解码件（`tools/train_leg_preview.py`）。

    python tools/shining_head_packet.py
"""
import collections
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import train_leg_preview as tlp  # noqa: E402


def bonds_of(ci):
    out = {}
    for e in (ci.get("evaluation_info_array") or []):
        if not isinstance(e, dict):
            continue
        pid = e.get("training_partner_id")
        if isinstance(pid, int):
            out[pid] = int(e.get("evaluation") or 0)
    return out


def main():
    rows = []
    for f, data in tlp.decode_all():
        cia = (data.get("home_info") or {}).get("command_info_array")
        if not cia:
            continue
        ci = data.get("chara_info") or {}
        turn = ci.get("turn")
        if not isinstance(turn, int):
            continue
        bonds = bonds_of(ci)
        for c in cia:
            cid = c.get("command_id")
            if cid not in tlp.PLAIN or not c.get("params_inc_dec_info_array"):
                continue
            pt, five, vit = tlp.preview_of(c)
            if pt is None:
                continue
            deck = [p for p in (c.get("training_partner_array") or [])
                    if isinstance(p, int) and p < 100]
            b80 = sum(1 for p in deck if bonds.get(p, 0) >= 80)
            rows.append((int(c.get("level") or 0), len(deck), b80, pt,
                         sum(v or 0 for v in five)))
    print("普通训练板条目（一手）：%d 条" % len(rows))
    if not rows:
        return 0

    print("\n== 羁绊分布（卡组人头里 evaluation 的形态）==")
    share = collections.Counter()
    for lv, nd, b80, pt, fs in rows:
        share[(nd, b80)] += 1
    tot = len(rows) or 1
    for (nd, b80), k in sorted(share.items()):
        print("  %d 人头其中 %d 闪彩：%5d 条 (%4.1f%%)" % (nd, b80, k, 100.0 * k / tot))

    print("\n== PT 中位：按 (训练Lv, 闪彩人头 b80) ==")
    b = collections.defaultdict(list)
    for lv, nd, b80, pt, fs in rows:
        b[(lv, b80)].append(pt)
    lvs = sorted({k[0] for k in b})
    print("Lv\\b80  " + "".join("%8d" % h for h in range(0, 5)))
    for lv in lvs:
        line = []
        for h in range(0, 5):
            v = b.get((lv, h))
            line.append("%8.1f" % statistics.median(v) if v and len(v) >= 15 else "       -")
        print("%-7s" % lv + "".join(line))

    print("\n== 同一 Lv 内，b80 每 +1 的 PT 倍率（真机）==")
    for lv in lvs:
        seq = [(h, statistics.median(b[(lv, h)])) for h in range(0, 5)
               if b.get((lv, h)) and len(b[(lv, h)]) >= 15]
        if len(seq) < 3:
            continue
        ratios = [seq[i + 1][1] / seq[i][1] for i in range(len(seq) - 1) if seq[i][1]]
        print("  Lv%-2s %s ⇒ 平均倍率 %.2f/头" % (
            lv, " → ".join("%d头:%.0f" % s for s in seq), statistics.fmean(ratios)))
    print("\n引擎（强制全闪彩，壳内单测实测）：7 → 17 → 43 → 117 ⇒ 平均倍率 2.55/头")
    return 0


if __name__ == "__main__":
    sys.exit(main())
