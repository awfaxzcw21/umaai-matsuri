#!/usr/bin/env python3
"""#88 稽古失败模型三臂配对读数：A＝现行（掷骰＋「無茶は厳禁！」倒扣）
B＝`UMA_KEIKO_NO_FAIL=1`（稽古腿不掷失败）
C＝`UMA_KEIKO_FAIL_NOPENALTY=1`（照掷，但失败时什么都不发生）

输入是 `out/legcell_ab/dump_AB_<arm>_s<1..5>.jsonl` 与 `dump_C_s<1..5>.jsonl`，
每行一局，字段 `i/sum/stat/pt/real/grade/five/act/gauge/gain/native`。
**配对**靠 `(片号 s, 行内 i)`——同一片、同一个 i 在两臂里是**同一批初始局**
（`MC_SEED=s MC_N=30` 定种子，只有失败模型那一行代码不同）。

输出三样，口径写死不许混：
  ① 三分量（五维和 / 五维分 / PT）的**配对均值差 + 95% t 区间**（AGENTS.md 的门禁口径，
     但这里跑在测量壳上，所以只当「这条改动值多少」的证据，**不是门禁通过**）；
  ② `real` 分（面板同口径终值）的配对差 + 区间 + `P(real>=85600)` 逐臂；
  ③ 稽古手数/失败手/腿均值（`keiko_cells`），确认 B/C 各自动了什么、没动什么。

只读 JSON，不写 `data/`、不改常数、不跑主树。
"""
from __future__ import annotations

import glob
import json
import math
import os
import statistics as st
import sys

LG15 = 85600
COMPONENTS = [("sum", "五维和"), ("stat", "五维分"), ("pt", "PT"), ("real", "real 分")]


def tcrit(df):
    """双侧 95% 的 t 临界值：df>=120 用 1.98，小的话退到正态近似（表已足够保守）。"""
    if df >= 200:
        return 1.972
    if df >= 120:
        return 1.979
    if df >= 60:
        return 2.000
    if df >= 30:
        return 2.042
    return 2.576


def load(arm, base):
    out = {}
    for p in sorted(glob.glob(os.path.join(base, f"dump_{arm}_s*.jsonl"))):
        shard = int(os.path.basename(p).split("_s")[1].split(".")[0])
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                out[(shard, r["i"])] = r
    return out


def paired(a, b, label_a, label_b):
    keys = sorted(set(a) & set(b))
    print(f"\n== 配对：{label_a} vs {label_b} ｜可比 {len(keys)} 局"
          f"（{len(a)}/{len(b)}）==")
    if not keys:
        print("  没有交集，先看片号/行数是不是对不上")
        return
    print(f"  {'量':<8} {label_a:>12} {label_b:>12} {'配对均值差':>12} "
          f"{'95% t 区间':>22} {'t':>7}")
    for key, name in COMPONENTS:
        va = [a[k][key] for k in keys]
        vb = [b[k][key] for k in keys]
        d = [y - x for x, y in zip(va, vb)]
        n = len(d)
        md = st.mean(d)
        sd = st.pstdev(d) * math.sqrt(n / (n - 1)) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n else 0.0
        t = md / se if se else 0.0
        h = tcrit(n - 1) * se if se else 0.0
        print(f"  {name:<8} {st.mean(va):>12,.0f} {st.mean(vb):>12,.0f} {md:>+12,.1f} "
              f"  [{md - h:>+10,.1f}, {md + h:>+9,.1f}] {t:>7.2f}")
    for key, name in (("grade", "Σgrade"), ("native", "native 分")):
        if key in a[keys[0]] and key in b[keys[0]]:
            va = [a[k][key] for k in keys]
            vb = [b[k][key] for k in keys]
            print(f"  {name:<8} {st.mean(va):>12,.0f} {st.mean(vb):>12,.0f}")
    for tag, m in ((label_a, a), (label_b, b)):
        hits = sum(1 for k in keys if m[k]["real"] >= LG15)
        print(f"  P(real>={LG15:,}) {tag} = {hits}/{len(keys)}")


def cells(arm, base):
    tot = {"hands": 0.0, "failed": 0.0, "home": 0.0, "festa": 0.0, "runs": 0}
    for p in sorted(glob.glob(os.path.join(base, f"res_{arm}_s*.json"))):
        r = json.load(open(p, encoding="utf-8"))
        n = r["n"]
        tot["runs"] += n
        cs = r["keiko_cells"]["cells"]
        h = sum(c["hands_per_run"] * n for c in cs)
        f = sum(c["failed_per_run"] * n for c in cs)
        ok = h - f
        tot["hands"] += h
        tot["failed"] += f
        tot["home"] += sum(c["home_leg_pt"] * (c["hands_per_run"] - c["failed_per_run"]) * n
                           for c in cs)
        tot["festa"] += sum(c["festa_leg_pt"] * (c["hands_per_run"] - c["failed_per_run"]) * n
                            for c in cs)
    if not tot["runs"]:
        return None
    n = tot["runs"]
    okh = tot["hands"] - tot["failed"]
    return dict(runs=n, hands=tot["hands"] / n, failed=tot["failed"] / n,
                rate=tot["failed"] / tot["hands"] if tot["hands"] else 0.0,
                home=tot["home"] / okh if okh else 0.0,
                festa=tot["festa"] / okh if okh else 0.0,
                legpt=(tot["home"] + tot["festa"]) / n)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "out/legcell_ab"
    arms = {}
    for tag, arm in (("A 现行", "AB_fail"), ("B 不掷失败", "AB_nofail"), ("C 掷而不罚", "C")):
        m = load(arm, base)
        if m:
            arms[tag] = m
        else:
            print(f"（{tag}：{base}/dump_{arm}_s*.jsonl 还没有，跳过）")
    print(f"== 稽古腿自身（{base}/res_*.json）==")
    for tag, arm in (("A 现行", "AB_fail"), ("B 不掷失败", "AB_nofail"), ("C 掷而不罚", "C")):
        c = cells(arm, base)
        if c:
            print(f"  {tag:<10} {c['runs']} 局  稽古全手 {c['hands']:.2f}/局 "
                  f"失败 {c['failed']:.2f}/局（{c['rate']*100:.1f}%） "
                  f"成功腿均值 home {c['home']:.1f} + festa {c['festa']:.1f} "
                  f"⇒ 腿 PT {c['legpt']:,.0f}/局")
    keys = list(arms)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            paired(arms[keys[i]], arms[keys[j]], keys[i], keys[j])
    print("\n注：跑在测量壳上 ⇒ 这是「值多少」的证据，不是门禁通过；"
          "落地要在主树跑 tools/crn_gate.py 的三分量配对区间。")


if __name__ == "__main__":
    main()
