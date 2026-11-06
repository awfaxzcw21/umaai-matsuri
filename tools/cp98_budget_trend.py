#!/usr/bin/env python3
"""§32 判据：**跨搜索预算的配对趋势检验**。

§26.3 写过一条「效应随 MC_K 单调升：+4,115(K=8) → +4,295(K=32) → +4,686(K=128)，
朝着壳上那个 +5,244 靠」。那条是**拿各档自己的点估计排队伍**，而四档的样本不一样
（192/192/32/24 局、分片数也不同）⇒ 排出来的可能是样本差而不是预算差。

本脚本把它变成可判的形式：只取**四档都跑过的同一批初始局**（seed 与局序 i 都相同，
CRN 下即同一局），逐局算 (B−A)，再对**同一局的两个预算**做配对差。
「随预算涨」必须在这张表里显著为正，否则那句话作废。

    python tools/cp98_budget_trend.py [--dir E:/HJM/_wt_cp98/out/cp98] [--metric stat_score]
"""
import argparse
import glob
import json
import math
import os
import statistics as st

KS = [8, 32, 128, 1024]


def load(d, K, prefix):
    """{(seed,i): {"A": rec, "B": rec}}，只留双臂都齐的局。"""
    out = {}
    for f in glob.glob(os.path.join(d, f"{prefix}{K}_*_s*.jsonl")):
        arm = "A" if "_A_" in os.path.basename(f) else "B"
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            out.setdefault((r["seed"], r["i"]), {})[arm] = r
    return {k: v for k, v in out.items() if len(v) == 2}


def ci(vals):
    n = len(vals)
    m = st.mean(vals)
    if n < 2:
        return m, float("nan"), float("nan"), float("nan"), n
    s = st.stdev(vals)
    tc = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571}.get(n - 1)
    if tc is None:
        tc = 2.069 if n <= 30 else 1.96
    half = tc * s / math.sqrt(n)
    return m, m - half, m + half, (m / (s / math.sqrt(n)) if s else float("nan")), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="E:/HJM/_wt_cp98/out/cp98")
    ap.add_argument("--prefix", default="t1K")
    ap.add_argument("--metric", default="stat_score")
    a = ap.parse_args()

    runs = {K: load(a.dir, K, a.prefix) for K in KS}
    print("各档配对上的局数：" + "  ".join(f"K={K}:{len(runs[K])}" for K in KS if runs[K]))
    have = [K for K in KS if runs[K]]
    keys = sorted(set.intersection(*[set(runs[K]) for K in have]))
    if not keys:
        raise SystemExit("四档没有任何共同的初始局，配对检验做不了（说明 CRN 键没对齐）")
    print(f"\n★只用**同一批初始局**（seed 与局序都相同）：{len(keys)} 局"
          f"（各档全样本分别 192/192/32/24 ⇒ 这里砍到公共集，才是可比的）\n")

    diff = {K: [runs[K][k]["B"][a.metric] - runs[K][k]["A"][a.metric] for k in keys] for K in have}
    print(f"每档自身（同这批局，{a.metric} 的 B−A）：")
    for K in have:
        m, lo, hi, t, n = ci(diff[K])
        print(f"  K={K:<5} {m:>8.0f} [{lo:+.0f}, {hi:+.0f}]  t={t:+5.2f}  n={n}")

    print("\n跨档配对（同一局在两档下的 (B−A) 之差；正＝高档效应更大＝§26.3 那句话要的）：")
    print(f"{'从':>7}{'到':>7}{'Δ':>10}{'95% 区间':>24}{'t':>7}   判定")
    sig_up = 0
    npair = 0
    for i in range(len(have)):
        for j in range(i + 1, len(have)):
            K1, K2 = have[i], have[j]
            m, lo, hi, t, n = ci([diff[K2][k] - diff[K1][k] for k in range(len(keys))])
            verdict = "更高（显著）" if lo > 0 else ("更低（显著）" if hi < 0 else "不显著")
            sig_up += lo > 0
            npair += 1
            print(f"{K1:>7}{K2:>7}{m:>10.0f}   [{lo:+8.0f}, {hi:+8.0f}]{t:>7.2f}   {verdict}")
    print(f"\n⇒ 判据：{npair} 个配对比较里，显著为正的 {sig_up} 个。")
    print("   若为 0，则「效应随搜索预算单调升、朝着壳上 +5,244 靠」**作废**——"
          "那是拿不同样本的点估计排出来的队伍，不是预算造成的。")


if __name__ == "__main__":
    main()
