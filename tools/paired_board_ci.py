#!/usr/bin/env python3
"""配对档间对照：强策档（2-ply 搜索）vs 基策（rollout），同一批初始局。

为什么要它：`merge_trainboard_arms.py` 合出来的是**点估计**，看不出差异是不是噪声。
盘面打点的分母是「一手决策」，跨局不同质，没法直接对单手机制开方。这里的配对单位取
**种子分片**：`MC_SEED=s MC_N=N` 的臂跑的那 N 局，两档吃到的是同一批
`seed = s·C1 + i·C2` 初始局（`dist.rs` 的种子式），所以同一片的 offer 分布几乎逐字相同
（站位只由卡组决定，与决策无关）⇒ 两档 take 之差是**配对差**，可以直接做 t 区间。

判据只看一件事：**选位增益 = take / offer最满** 在两档之间有没有动。
基策档 0.576 / 真机 0.95 的缺口，如果强策档把它抬起来了 ⇒ 缺口记在「策略没学」；
抬不动 ⇒ 记在「估值看不见人头」，改决策代码之前得先改估值。

用法：
    python tools/paired_board_ci.py \
        --strong E:/HJM/_keileg/out/s41_k8_s*.json \
        --base   E:/HJM/_keileg/out/s41_pbase_s*.json
"""
import argparse
import glob
import json
import math
import sys

REAL_GAIN = 0.95  # #40 真机口径：落子站位 / 盘面最满（`train_leg_gap_split.py` 同一算法）

# 单尾 97.5% 的 Student-t 分位数，df>=30 用正态近似
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
        14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
        20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060,
        26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045}


def t975(df):
    return T975.get(df, 1.960)


def gain_from_cols(rows):
    """两层 20 列原始计数（已跨片相加）-> (take, offer最满, 增益, 取最满占比, 手/板)。
    列序 = `train_legs::BOARD_COLS`，公式与 `dist.rs::train_board_mean` 一致。"""
    boards = sum(float(r[0]) for r in rows)
    chosen = sum(float(r[10]) for r in rows)
    take = sum(sum(h * r[11 + h] for h in range(8)) for r in rows) / max(chosen, 1.0)
    offer = sum(sum(h * r[1 + h] for h in range(8)) for r in rows) / max(boards, 1.0)
    return take, offer, take / max(offer, 1e-12), sum(r[19] for r in rows) / max(chosen, 1.0)


def shard_stats(j):
    """一个分片（一档 × 一个种子 × N 局）的读数：留原始列，合并时才精确。"""
    raw = j["train_board_raw"]["per_layer"]
    n = int(j["train_board_raw"]["n_runs"])
    take, offer, gain, tm_share = gain_from_cols(raw)
    hands = sum(float(r[10]) for r in raw)
    boards = sum(float(r[0]) for r in raw)
    g = j.get("gate", {})
    return {"seed": j["seed_base"], "n": n, "raw": raw, "hands": hands, "boards": boards,
            "take": take, "offer": offer, "gain": gain, "take_max_share": tm_share,
            "hands_per_run": hands / max(n, 1),
            "gate": {k: g.get(k, {}).get("mean", float("nan"))
                     for k in ("five_sum", "stat_score", "pt")}}


def load(globp):
    out = {}
    for p in sorted(glob.glob(globp)):
        v = shard_stats(json.load(open(p, encoding="utf-8")))
        if v["seed"] in out:
            sys.exit("同一种子有两片：%s（分片命名要唯一）" % v["seed"])
        out[v["seed"]] = v
    if not out:
        sys.exit("没吃到分片：%s" % globp)
    return out


def pool(arm, layer=None):
    """跨分片按原始总量合并（不是分片均值的均值）。`layer=None` 两层合起来。"""
    groups = [range(2)] if layer is None else [[layer]]
    acc = []
    for grp in groups:
        row = [0] * 20
        for v in arm.values():
            for i in grp:
                for c, x in enumerate(v["raw"][i]):
                    row[c] += x
        acc.append(row)
    return gain_from_cols(acc)


def gate_pool(arm, field):
    return sum(v["gate"][field] * v["n"] for v in arm.values()) / max(
        sum(v["n"] for v in arm.values()), 1)


def paired(a, b, key):
    """配对差 a−b（同种子同局集）-> (点差, 95%区间, 是否显著, 单位数)。"""
    d = [key(a[k]) - key(b[k]) for k in sorted(a) if k in b]
    n = len(d)
    if n < 2:
        return float("nan"), (float("nan"), float("nan")), False, n
    mean = sum(d) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1))
    half = t975(n - 1) * sd / math.sqrt(n)
    lo, hi = mean - half, mean + half
    return mean, (lo, hi), not (lo <= 0.0 <= hi), n


def ci(d):
    """一组配对差的均值 ± 95% t 区间。"""
    n = len(d)
    if n < 2:
        return float("nan"), (float("nan"), float("nan")), n
    mean = sum(d) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in d) / (n - 1))
    half = t975(n - 1) * sd / math.sqrt(n)
    return mean, (mean - half, mean + half), n


def ladder(globp):
    """估值阶梯的单档读数 + 按种子分片的 95% 区间（`arm_val41b.exe` 的分片）。
    配对单位 = 同一批根决策里的 `盘面最满人头 − 估值最优人头`：同片同根，天然配对。"""
    shards = []
    for p in sorted(glob.glob(globp)):
        j = json.load(open(p, encoding="utf-8"))
        tv = j.get("train_value_raw")
        if not tv:
            sys.exit("%s 没有 train_value_raw（要 arm_val41b.exe 起的臂）" % p)
        shards.append((tv["per_layer"], int(tv["n_runs"])))
    if not shards:
        sys.exit("没吃到分片：%s" % globp)
    games = sum(n for _r, n in shards)
    tot = [[0.0] * 30, [0.0] * 30]
    for rows, _n in shards:
        for i in range(2):
            for c, x in enumerate(rows[i]):
                tot[i][c] += x
    print("估值阶梯：%d 片 / %d 局（配对单位=种子分片，n=%d）" % (len(shards), games, len(shards)))
    print("%-9s %7s %10s %10s %12s %16s" % ("层", "根/局", "盘面最满", "估值最优", "阶梯落差", "一致率(τ)"))
    for i, lname in enumerate(("non_camp", "camp")):
        r = tot[i]
        roots = max(r[0], 1.0)
        conc, disc, tie = r[11], r[12], r[13]
        def key(sh, i=i):
            x = sh[0][i]
            return (x[10] - x[9]) / max(x[0], 1.0)
        mean, (lo, hi), n = ci([key(sh) for sh in shards])
        print("%-9s %7.1f %10.3f %10.3f %12.3f %8.1f%% (%.3f)"
              % (lname, roots / max(games, 1), r[10] / roots, r[9] / roots,
                 (r[10] - r[9]) / roots, 100.0 * conc / max(conc + disc, 1.0),
                 (conc - disc) / max(conc + disc + tie, 1.0)))
        print("          落差 95%%CI [%+.3f, %+.3f] (n=%d)" % (lo, hi, n))
        for h in range(1, 4):
            def step(sh, h=h, i=i):
                x = sh[0][i]
                if x[22 + h] <= 0 or x[21 + h] <= 0:
                    return None
                return x[14 + h] / x[22 + h] - x[13 + h] / x[21 + h]
            vals = [v for v in (step(sh) for sh in shards) if v is not None]
            if len(vals) >= 2:
                m, (lo, hi), n = ci(vals)
                print("          人头 %d→%d 估值步长 %+8.1f  95%%CI [%+.1f, %+.1f] (n=%d，整局分)"
                      % (h - 1, h, m, lo, hi, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strong", help="强策档分片 glob")
    ap.add_argument("--base", help="基策档分片 glob（同一批初始局）")
    ap.add_argument("--ladder", help="只跑估值阶梯：传 arm_val41b 分片的 glob")
    ap.add_argument("--label", default="", help="表头备注，如 K=8")
    ap.add_argument("--json", help="可选：把增益配对差写成 JSON")
    a = ap.parse_args()

    if a.ladder:
        ladder(a.ladder)
        return
    if not (a.strong and a.base):
        sys.exit("要么给 --ladder，要么同时给 --strong 和 --base")

    s, b = load(a.strong), load(a.base)
    for tag, arm in (("基策", b), ("强策", s)):
        seeds = sorted(arm)
        print("%s档%s：种子 %d 片（%d…%d）/ %d 局；每片局数 %s"
              % (tag, a.label, len(seeds), seeds[0], seeds[-1],
                 sum(arm[k]["n"] for k in seeds),
                 sorted({arm[k]["n"] for k in seeds})))
    common = sorted(set(s) & set(b))
    if not common:
        sys.exit("两档没有共同种子，配对不成立")
    if len(common) < min(len(s), len(b)):
        print("⚠ 只有 %d 个种子两档齐全，其余丢弃" % len(common))
    s = {k: s[k] for k in common}
    b = {k: b[k] for k in common}
    if {s[k]["n"] for k in common} != {b[k]["n"] for k in common}:
        print("⚠ 同种子两档局数不同：配对只保证初始局相同，不保证局数相同")

    print("\n%-20s %10s %10s %18s %12s" % ("量", "基策档", "强策档", "配对差 95%CI", "判读"))
    hands = lambda arm: sum(v["hands"] for v in arm.values()) / max(  # noqa: E731
        sum(v["n"] for v in arm.values()), 1)
    for label, field, dec, col in (("选位增益 take/offer", "gain", 4, 2),
                                   ("落子人头 take", "take", 4, 0),
                                   ("盘面最满 offer", "offer", 4, 1),
                                   ("取最满占比", "take_max_share", 4, 3)):
        key = lambda v, f=field: v[f]  # noqa: E731
        pb, ps = pool(b)[col], pool(s)[col]
        d, ci, sig, n = paired(s, b, key)
        print("%-20s %*.*f %*.*f  [%+.4f, %+.4f] %s (n=%d)"
              % (label, 10, dec, pb, 10, dec, ps, ci[0], ci[1],
                 "显著" if sig else "不显著（区间过零）", n))
    for label, field, dec in (("训练手/局", "hands_per_run", 3),
                              ("五维和", "five_sum", 1),
                              ("五维分", "stat_score", 1),
                              ("PT", "pt", 1)):
        if field == "hands_per_run":
            key = lambda v: v["hands_per_run"]  # noqa: E731
            pb, ps = hands(b), hands(s)
        else:
            key = lambda v, f=field: v["gate"][f]  # noqa: E731
            pb, ps = gate_pool(b, field), gate_pool(s, field)
        d, ci, sig, n = paired(s, b, key)
        print("%-20s %*.*f %*.*f  [%+.4f, %+.4f] %s (n=%d)"
              % (label, 10, dec, pb, 10, dec, ps, ci[0], ci[1],
                 "显著" if sig else "不显著（区间过零）", n))

    print("\n分层（非合宿=普通训练回合，合宿=稽古回合）")
    for i, lname in enumerate(("non_camp", "camp")):
        def key(v, i=i):
            return gain_from_cols([v["raw"][i]])[2]
        d, ci, sig, n = paired(s, b, key)
        print("  %-9s 增益 基策 %.3f -> 强策 %.3f  差 [%+.3f, %+.3f] %s"
              % (lname, pool(b, i)[2], pool(s, i)[2], ci[0], ci[1],
                 "显著" if sig else "不显著"))

    gain_b, gain_s = pool(b)[2], pool(s)[2]
    rec = (gain_s - gain_b) / (REAL_GAIN - gain_b)
    print("\n真机口径（#40）：选位增益 %.2f｜基策档 %.3f｜强策档 %.3f"
          % (REAL_GAIN, gain_b, gain_s))
    print("  缺口回收 %.1f%% ⇒ 剩 %.1f%% 不是「策略没学到」能解释的，指向估值层"
          % (100 * rec, 100 * (1 - rec)))
    print("  ⚠ 强策档的 K 若远低于面板默认 1024，本行只说明「搜索看见了多少」，"
          "不等于面板实机读数；要配 K 趋势一起读")
    if a.json:
        d, ci, sig, n = paired(s, b, lambda v: v["gain"])
        json.dump({"label": a.label, "base_gain": gain_b, "strong_gain": gain_s,
                   "paired_diff": d, "ci95": list(ci), "significant": sig,
                   "units": n, "recover_share": rec, "seeds": common},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
