#!/usr/bin/env python3
"""#48 発表会兜底的代价：同 exe 开/关兜底的配对 CRN 读数。

面板那条决策链在搜索之外还挂了一条**兜底**（`searcharm.rs::forced_target`）：
临近检查点且有演目未达标 ⇒ 强行把落子改成「最接近达标」的那格，搜索分数让位。
#41 量到合宿层的**实际落子人头低于估值排序**（1.145 → 0.902），怀疑就是它。

但只看人头是半盲的：発表会在引擎里是**档位结算**（`min(全演目 Lv) ≥ need` ⇒ 大成功，
否则成功档；到回合必结算，不存在失败），兜底可能买的是**大成功 buff**而不是白丢人头。
所以本脚本同时读三样：
  ① 三分量门禁（五维和 / 五维分 / PT）配对差 + 95% 区间
  ② 选位增益（`train_value_raw`：take = 实际落子人头、offer = 估值最优 / 盘面最满）
  ③ 每场检查点的 大成功率 / `min(Lv)−need` 均值 / 未达标演目数 + 兜底触发次数

配对单位 = 种子分片（同 `MC_SEED` ⇒ 同一批初始局）。**双臂同侧不结论**：A 臂（兜底开）
与 B 臂（关）都来自同一 exe，只是环境变量不同 ⇒ 差异只归于兜底这一处改动。

用法：
    python tools/forced_cp_ci.py \\
        --a E:/HJM/_keileg/out/s48_A_s*.json \\
        --b E:/HJM/_keileg/out/s48_B_s*.json
"""
import argparse
import glob
import json
import math
import os
import re
import sys

T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 30: 2.042, 40: 2.021, 60: 2.000}


def t975(df):
    if df in T975:
        return T975[df]
    ks = sorted(T975)
    if df > ks[-1]:
        return 1.960
    if df < ks[0]:
        return T975[ks[0]]
    lo = max([k for k in ks if k <= df])
    hi = min([k for k in ks if k >= df])
    if lo == hi:
        return T975[lo]
    f = (df - lo) / (hi - lo)
    return T975[lo] * (1 - f) + T975[hi] * f


def ci(vals):
    """均值 + 单样本 95% t 区间；返回 (mean, (lo, hi), n)。"""
    v = [x for x in vals if x is not None and not math.isnan(x)]
    n = len(v)
    if n < 2:
        return (v[0] if v else float("nan"), (float("nan"), float("nan")), n)
    m = sum(v) / n
    s = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    h = t975(n - 1) * s / math.sqrt(n)
    return m, (m - h, m + h), n


def load(pattern, label):
    ps = sorted(glob.glob(pattern))
    if not ps:
        sys.exit("%s 臂没吃到分片：%s" % (label, pattern))
    out = {}
    for p in ps:
        j = json.load(open(p, encoding="utf-8"))
        seed = int(j["seed_base"])
        if seed in out:
            sys.exit("%s 臂种子 %d 重复（两批分片混了，判据作废）" % (label, seed))
        out[seed] = j
    return out


def value_rows(j):
    tv = j.get("train_value_raw")
    if not tv:
        sys.exit("%s 没有 train_value_raw（要 arm_cp48.exe 起的臂）" % tv)
    return tv["per_layer"], int(tv["n_runs"])


def per_shard(j):
    """一片的读数：层内 offer/take、门禁三分量、CP 与兜底计数。"""
    rows, n = value_rows(j)
    d = {"n": n}
    for i, lname in enumerate(("non_camp", "camp")):
        r = rows[i]
        roots = r[0]
        d[lname] = {
            "roots": roots,
            "offer_max": r[10] / roots if roots else float("nan"),   # 盘面最满人头
            "value_best": r[9] / roots if roots else float("nan"),   # 估值最优格人头
            "take": float("nan"),     # 实际落子人头在 BOARD 表里，下面填
            "offer_board": float("nan"),
        }
    g = j["gate"]
    d["five"] = g["five_sum"]["mean"]
    d["stat"] = g["stat_score"]["mean"]
    d["pt"] = g["pt"]["mean"]
    d["forced"] = j["search_diag"]["forced_by_festa_deadline"] / max(n, 1)
    d["train_steps"] = j["search_diag"]["train_steps"] / max(n, 1)
    board = j.get("train_board_raw", {}).get("per_layer")
    if board:
        for i, lname in enumerate(("non_camp", "camp")):
            b = board[i]
            chosen = b[10]
            d[lname]["take"] = (sum(h * b[11 + h] for h in range(8)) / chosen) if chosen else float("nan")
            d[lname]["offer_board"] = (sum(h * b[1 + h] for h in range(8)) / b[0]) if b[0] else float("nan")
    cp = j.get("check_point_raw")
    if cp:
        d["cp"] = cp["per_slot"]
        d["cp_turns"] = cp.get("turns")
        d["cp_needs"] = cp.get("needs")
    return d


def paired(a, b, key):
    va, vb = [], []
    for s in sorted(set(a) & set(b)):
        try:
            x = key(a[s])
            y = key(b[s])
        except (KeyError, TypeError):
            continue
        if x is None or y is None or math.isnan(x) or math.isnan(y):
            continue
        va.append(x)
        vb.append(y)
    diffs = [y - x for x, y in zip(va, vb)]
    return va, vb, diffs


def report(name, a, b, key, fmt="%8.3f"):
    va, vb, diffs = paired(a, b, key)
    if len(diffs) < 2:
        print("%-26s 配对样本不足（n=%d）" % (name, len(diffs)))
        return
    ma, lohi, n = ci(diffs)
    print("%-26s A=%s B=%s  差(B−A)=%s 95%%CI [%s, %s] n=%d"
          % (name, fmt % (sum(va) / len(va)), fmt % (sum(vb) / len(vb)),
             ("%+.3f" % ma) if abs(ma) < 1000 else ("%+.1f" % ma),
             ("%+.3f" % lohi[0]) if abs(lohi[0]) < 1000 else ("%+.1f" % lohi[0]),
             ("%+.3f" % lohi[1]) if abs(lohi[1]) < 1000 else ("%+.1f" % lohi[1]), n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="兜底开（默认）臂的分片 glob")
    ap.add_argument("--b", required=True, help="MC_NO_FORCED=1 臂的分片 glob")
    ap.add_argument("--da", default=None,
                    help="A 臂 `MC_DUMP` 逐局 jsonl glob；给了就加一段**逐局**配对（功效更高，但配对单位比规定的分片更细，只作辅证）")
    ap.add_argument("--db", default=None, help="B 臂逐局 jsonl glob")
    args = ap.parse_args()

    A, B = load(args.a, "A"), load(args.b, "B")
    sa = {s: per_shard(j) for s, j in A.items()}
    sb = {s: per_shard(j) for s, j in B.items()}
    common = sorted(set(sa) & set(sb))
    print("配对分片：%d 片（A 有 %d、B 有 %d，交集外的不结论）"
          % (len(common), len(sa), len(sb)))
    print("每片局数 A=%s B=%s"
          % ([sa[s]["n"] for s in common][:4], [sb[s]["n"] for s in common][:4]))

    # CRN 自检：offer（盘面最满人头）只由站位决定 ⇒ 双臂应逐字相同
    print("\n== CRN 自检（盘面最满人头：站位决定，与决策无关 ⇒ 配对差应为 0）==")
    for lname in ("non_camp", "camp"):
        def k(d, lname=lname):
            return d[lname].get("offer_board", float("nan"))
        report("offer_board " + lname, sa, sb, k, "%8.4f")

    print("\n== ① 三分量门禁（配对差 B−A，95% 区间不含 0 才算动）==")
    for nm, key in (("五维和", "five"), ("五维分", "stat"), ("PT", "pt")):
        report(nm, sa, sb, lambda d, key=key: d[key], "%9.1f")

    print("\n== ② 选位增益 take/offer 与阶梯 ==")
    for lname in ("non_camp", "camp"):
        def gain(d, lname=lname):
            x = d[lname]
            return x["take"] / x["offer_board"] if x.get("offer_board") else float("nan")
        report("选位增益 " + lname, sa, sb, gain, "%8.4f")

        def step(d, lname=lname):
            x = d[lname]
            return x["take"] - x["value_best"] if x["value_best"] == x["value_best"] else float("nan")
        report("实际−估值最优 " + lname, sa, sb, step, "%+8.3f")

        def vsboard(d, lname=lname):
            x = d[lname]
            return x["value_best"] - x["offer_max"]
        report("估值最优−最满 " + lname, sa, sb, vsboard, "%+8.3f")

    print("\n== ③ 兜底触发与训练手 ==")
    report("兜底触发 次/局", sa, sb, lambda d: d["forced"], "%8.2f")
    report("训练决策 手/局", sa, sb, lambda d: d["train_steps"], "%8.2f")

    print("\n== ④ 每场检查点（汇总：A 臂全片 / B 臂全片）==")
    for lbl, ss, dd in (("A 兜底开", common, sa), ("B 关兜底", common, sb)):
        cps = [d["cp"] for s in ss for d in [dd[s]] if "cp" in d]
        if not cps:
            print("%s：分片里没有 check_point_raw ⇒ 这批是旧 exe，作废" % lbl)
            continue
        turns = dd[common[0]].get("cp_turns")
        needs = dd[common[0]].get("cp_needs")
        print("%s（局数 %d）" % (lbl, sum(dd[s]["n"] for s in ss)))
        print("  %-10s %6s %8s %10s %12s %10s"
              % ("场(turn/need)", "结算", "大成功率", "平均margin", "平均未达标格", "买到的档"))
        for i in range(4):
            n = sum(c[i][0] for c in cps)
            if n <= 0:
                continue
            gr = sum(c[i][1] for c in cps)
            mg = sum(c[i][2] for c in cps)
            df_ = sum(c[i][3] for c in cps)
            print("  %-10s %6.0f %7.1f%% %10.3f %12.3f %10s"
                  % ("t%s/%s" % (turns[i] if turns else i, needs[i] if needs else "?"),
                     n, 100.0 * gr / n, mg / n, df_ / n,
                     "大成功" if gr / n > 0.999 else ("成功档" if gr / n < 0.001 else "混合")))
    # 配对：每片大成功率差
    print("\n  配对差（B−A）按片：")
    for i in range(4):
        def rate(d, i=i):
            c = d.get("cp")
            return (c[i][1] / c[i][0]) if c and c[i][0] > 0 else None
        report("第 %d 场大成功率" % (i + 1), sa, sb, rate, "%8.3f")
        def marg(d, i=i):
            c = d.get("cp")
            return (c[i][2] / c[i][0]) if c and c[i][0] > 0 else None
        report("第 %d 场 margin" % (i + 1), sa, sb, marg, "%+8.3f")

    if args.da and args.db:
        per_game(args.da, args.db)


def per_game(pa, pb):
    """辅证：配对单位=**逐局**（同片同 `i` ⇒ 同一初始局）。
    分片级是本仓规定判据；这段只用来回答「分片级 n 小时，效应是不是真的存在」。"""
    def rd(g):
        d = {}
        for p in sorted(glob.glob(g)):
            seed = int(re.search(r"_s(\d+)\.jsonl$", os.path.basename(p)).group(1))
            for line in open(p, encoding="utf-8"):
                if not line.strip():
                    continue
                r = json.loads(line)
                d[(seed, r["i"])] = r
        return d

    A, B = rd(pa), rd(pb)
    ks = sorted(set(A) & set(B))
    if len(ks) < 4:
        print("\n逐局配对：只有 %d 局公共键 ⇒ 跳过" % len(ks))
        return
    print("\n== 辅证：逐局配对（单位=初始局，n=%d；同片同 i ⇒ 同一初始局）==" % len(ks))
    print("   注意：局内独立性假设比分片强 ⇒ 区间偏窄，只作功效核对，不改判据")
    for nm, k in (("五维和", "sum"), ("五维分", "stat"), ("PT", "pt"),
                  ("档位估计", "grade"), ("引擎分", "native")):
        diffs = [B[key][k] - A[key][k] for key in ks]
        m, (lo, hi), n = ci(diffs)
        print("  %-8s Δ(B−A)=%9.1f 95%%CI [%+9.1f, %+9.1f] n=%d  中位 %+9.1f"
              % (nm, m, lo, hi, n, sorted(diffs)[n // 2]))
    # 兜底开时「赢的局」占比：配对差的符号比例（不看大小）
    for nm, k in (("五维和", "sum"), ("PT", "pt")):
        diffs = [B[key][k] - A[key][k] for key in ks]
        up = sum(1 for x in diffs if x > 0)
        dn = sum(1 for x in diffs if x < 0)
        print("  %-8s 关兜底更高 %d 局 / 更低 %d 局 / 持平 %d"
              % (nm, up, dn, len(diffs) - up - dn))


if __name__ == "__main__":
    main()
