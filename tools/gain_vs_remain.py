"""收益-剩余空间曲线：同一手训练内部，主轴吃满增量，其余四轴只有跨轴溢出。
把溢出轴和自己那一轴的「结算前剩余空间」配对，就在**同一回合、同一 run** 内做了对照——
剩下只有 run 间的卡组/buff 差异，没有回合趋势的混淆。

用来裁决：真机训练收益到底会不会随逼近上限而衰减（`cap_decay` 那套没有报文依据的拟合参数的替身）。

轴序不猜：`master.mdb single_mode_training_effect` 里 scenario 15 的 command→target_type 就是定义
（1 速 2 耐 3 力 4 根 5 智 10 体力 30 技能点），实测增量与该定义逐轴吻合。
"""

import collections
import json
import math
import sys

sys.path.insert(0, "tools")
from turn_gain import AX

# 单独练 / 合体练 → 主轴（由 single_mode_training_effect 的 target_type 定，再用实测增量复核）
MAIN = {101: 0, 105: 1, 102: 2, 103: 3, 106: 4, 5101: 0, 5102: 1, 5103: 2, 5104: 3, 5105: 4}
SPILLOVER = list(range(5))


def load():
    return json.load(open("out/_turn_gain.json", encoding="utf-8"))


def bucket_stats(pairs):
    """pairs: [(remain, delta)] → 分箱均值 + 秩相关 + 两端 Welch 样均值差"""
    if len(pairs) < 8:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    order = sorted(range(len(pairs)), key=lambda i: xs[i])
    n = len(order)
    buckets = []
    k = 5
    step = max(1, n // k)
    for b in range(0, n, step):
        idx = order[b:b + step]
        if len(idx) < 3:
            continue
        buckets.append((sum(xs[i] for i in idx) / len(idx), sum(ys[i] for i in idx) / len(idx), len(idx)))
    # Spearman
    def rank(v):
        s = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(s):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    den = math.sqrt(sum((rx[i] - mx) ** 2 for i in range(n)) * sum((ry[i] - my) ** 2 for i in range(n)))
    rho = cov / den if den else 0.0
    return {"buckets": buckets, "rho": rho, "n": n}


def fe_regression(points):
    """带 (run, command, 轴) 固定效应的单变量回归：组内去均值后斜率 = Σxy/Σxx，附朴素 SE。
    points: [(group_key, remain, delta)]
    """
    by = collections.defaultdict(list)
    for k, x, y in points:
        by[k].append((x, y))
    sxx = sxy = 0.0
    resid = []
    n = 0
    for vals in by.values():
        if len(vals) < 3:
            continue
        mx = sum(v[0] for v in vals) / len(vals)
        my = sum(v[1] for v in vals) / len(vals)
        for x, y in vals:
            sxx += (x - mx) ** 2
            sxy += (x - mx) * (y - my)
            n += 1
    if sxx == 0:
        return None
    beta = sxy / sxx
    for vals in by.values():
        if len(vals) < 3:
            continue
        mx = sum(v[0] for v in vals) / len(vals)
        my = sum(v[1] for v in vals) / len(vals)
        for x, y in vals:
            resid.append((y - my) - beta * (x - mx))
    s2 = sum(r * r for r in resid) / max(1, len(resid) - 1)
    se = math.sqrt(s2 / sxx)
    return {"beta": beta, "se": se, "n": n, "groups": len(by)}


def main():
    rows = load()
    solo = [r for r in rows if r["command"] in MAIN and r["command"] < 200]
    fused = [r for r in rows if r["command"] in MAIN and r["command"] >= 200]
    print(f"可归因回合 {len(rows)}：单独练 {len(solo)} 手 / 合体练 {len(fused)} 手")

    for name, group in (("单独练", solo), ("合体练", fused)):
        print(f"\n=== {name}：溢出轴的增量 vs 该轴自己的剩余空间（同一手内自对照）")
        allp = []
        for i in SPILLOVER:
            pairs = [(r["caps"][i] - r["before"][i], r["delta"][i]) for r in group if MAIN[r["command"]] != i]
            st = bucket_stats(pairs)
            allp += pairs
            if st:
                bs = "  ".join(f"[{rem:5.0f}]{g:+.1f}(n{n})" for rem, g, n in st["buckets"])
                print(f"  {AX[i]}: ρ={st['rho']:+.3f} n={st['n']:3d}  {bs}")
        st = bucket_stats(allp)
        if st:
            lo = [d for rem, d in allp if rem <= st["buckets"][0][0]]
            hi = [d for rem, d in allp if rem >= st["buckets"][-1][0]]
            print(f"  合计 n={st['n']} ρ={st['rho']:+.3f}   最贴箱均值 {sum(lo)/len(lo):.2f} vs 最空箱 {sum(hi)/len(hi):.2f}"
                  f"   差 {sum(hi)/len(hi) - sum(lo)/len(lo):+.2f} 点")

    print("\n=== 主轴增量 vs 自己的剩余空间（run×command×轴 固定效应，组内去均值）")
    pts = [(r["run"], r["command"], MAIN[r["command"]]) for r in solo + fused]
    pts = [(k, r["caps"][MAIN[r["command"]]] - r["before"][MAIN[r["command"]]], r["delta"][MAIN[r["command"]]])
           for r, k in zip(solo + fused, pts)]
    fe = fe_regression(pts)
    if fe:
        print(f"  斜率 β={fe['beta']:+.5f} 分/剩余点  se={fe['se']:.5f}  t={fe['beta'] / fe['se']:+.2f}  "
              f"n={fe['n']} 组数={fe['groups']}   ⇒ 每剩 100 点空间，增量差 {fe['beta'] * 100:+.1f} 点")
    for tag, grp in (("单独练", solo), ("合体练", fused)):
        fe2 = fe_regression([(r["run"], r["command"], MAIN[r["command"]]),
                             r["caps"][MAIN[r["command"]]] - r["before"][MAIN[r["command"]]],
                             r["delta"][MAIN[r["command"]]]] for r in grp)
        if fe2:
            print(f"  {tag}: β={fe2['beta']:+.5f} se={fe2['se']:.5f} t={fe2['beta'] / fe2['se']:+.2f} n={fe2['n']}")

    print("\n=== 夹逼到底咬不咬：结算后正好顶在上限上的回合")
    pinned = [r for r in solo + fused if any(r["after"][i] == r["caps"][i] for i in SPILLOVER)]
    for i in SPILLOVER:
        v = [r for r in solo + fused if r["after"][i] == r["caps"][i] and r["delta"][i] != 0]
        if v:
            mainish = sum(1 for r in v if MAIN[r["command"]] == i)
            print(f"  {AX[i]}: {len(v)} 手练完正好顶格（其中该轴是主轴 {mainish} 手）"
                  f" 这些手的增量 {sorted(r['delta'][i] for r in v)[:6]}…{sorted(r['delta'][i] for r in v)[-3:]}")

    print("\n=== 每回合收益表（按 command × run，主轴/次轴均值；次轴按 training_effect 定义列）")
    SEC = {101: 2, 5101: 2, 105: 3, 5102: 3, 102: 1, 5103: 1, 103: 2, 5104: 2, 106: 0, 5105: 0}
    g = collections.defaultdict(list)
    for r in solo + fused:
        g[(r["command"], r["run"])].append(r)
    for c in sorted({k[0] for k in g}):
        v = [r for k in g if k[0] == c for r in g[k]]
        m = MAIN[c]
        s = SEC.get(c)
        med = sorted(r["delta"][m] for r in v)
        print(f"  command {c:4d}（主轴{AX[m]}）n={len(v):3d} 主轴均值 {sum(r['delta'][m] for r in v) / len(v):6.2f} "
              f"中位 {med[len(med)//2]:5.1f} p10 {med[max(0, int(.1 * len(med)) - 1)]:5.1f} p90 {med[min(len(med) - 1, int(.9 * len(med)))]:5.1f}"
              + (f"   次轴{AX[s]}均值 {sum(r['delta'][s] for r in v) / len(v):5.2f}" if s is not None else ""))

    print("\n=== result_state（本批样本只出现 1/2）")
    for rs_ in sorted({r["result_state"] for r in rows if r["result_state"] is not None}):
        v = [r["delta"][MAIN[r["command"]]] for r in solo + fused if r["result_state"] == rs_]
        if v:
            print(f"  result_state {rs_}: n={len(v):3d} 主轴均值 {sum(v) / len(v):6.2f}")


if __name__ == "__main__":
    main()
