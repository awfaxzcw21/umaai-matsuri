#!/usr/bin/env python3
"""#100 读数：整局按面板建议打，A＝挂回 #98 删掉的发表会兜底重排，B＝不挂（＝发布的）。

三件事，一条命令出：
1. **逐场发表会大成功率**：配对（同一 CRN 种子＝同一局）四格表 + McNemar 精确二项 p。
   这是「关掉兜底档位会不会退化」的直接答案。
2. **三分量配对差**（五维和/五维分/PT）：95% t 区间，逐局配对。
3. **CRN 自检**：A 臂里 `overridden==0` 的那些局（兜底一次都没改写 top-1），
   两臂必须**逐字段相同**（同种子、同策略、同 rng 派生）。不相等就说明这台仪器
   的两臂不是同一批初始局 ⇒ 上面两条读数作废。

    python tools/cp98_tiers.py --a "E:/HJM/_wt_cp98/out/cp98/t1K32_A_s*.jsonl" \\
                              --b "E:/HJM/_wt_cp98/out/cp98/t1K32_B_s*.jsonl"
"""
import argparse
import glob
import math
import json
import statistics as st


def load(pats):
    rows = {}
    for pat in pats:
        for f in glob.glob(pat):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                rows[r["seed"]] = r
    return rows


def mcnemar_exact(b, c):
    """双侧精确二项：b=只有A大成功, c=只有B大成功。n=b+c 下 |偏离| ≥ 观察值的概率。"""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * p)


def paired_ci(diffs):
    n = len(diffs)
    if n < 2:
        return (float("nan"), float("nan"), float("nan"), n)
    m = st.mean(diffs)
    se = st.stdev(diffs) / math.sqrt(n)
    # df 大时 t≈1.96；小样本查表
    t = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571}.get(n - 1)
    if t is None:
        t = 1.96 if n >= 30 else _ttab(n - 1)
    return m, m - t * se, m + t * se, n


def _ttab(df):
    # 常用上分位（双侧 95%）
    tab = {6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
           13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
           20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
           27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042}
    return tab.get(df, 1.96 + 2.4 / max(df, 1) ** 1.2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", nargs="+", required=True, help="A 臂（兜底挂回）jsonl glob，可多个")
    ap.add_argument("--b", nargs="+", required=True, help="B 臂（发布后）jsonl glob，可多个")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    A, B = load(a.a), load(a.b)
    common = sorted(set(A) & set(B))
    print(f"### {a.tag}  A={len(A)} 局 B={len(B)} 局  配对上的={len(common)}")
    if not common:
        raise SystemExit("没有配对：种子对不上，检查 MC_SEED 分片")

    # ---- 3. CRN 自检 ----
    inert = [s for s in common if A[s]["overridden"] == 0]
    bad = []
    for s in inert:
        x, y = A[s], B[s]
        if x["cp_log"] != y["cp_log"] or (x["five_sum"], x["stat_score"], x["pt"]) != (y["five_sum"], y["stat_score"], y["pt"]):
            bad.append(s)
    pols = {A[s].get("policy") for s in common}
    if pols == {"base"}:
        inert = []  # 基策档两臂的差异本来就在策略里，这条自检只适用面板档
    if not inert and pols == {"base"}:
        print(f"[CRN 自检] 基策档（MC_POLICY=base）不适用这条自检：两臂差的就是策略本身，"
              "同批初始局由「同 MC_SEED 散列 + 同 exe」保证")
    elif not inert:
        print(f"[CRN 自检] A 臂兜底没改写任何一手的局 0/{len(common)} 个 ⇒ **这项自检空转**，"
              "没有『两臂应逐字相同』的样本；两臂同批初始局只能靠"
              "tools/cp98_instrument.py 的同种子重跑确定性来证")
    else:
        print(f"[CRN 自检] A 臂兜底没改写任何一手的局 {len(inert)}/{len(common)} 个，"
              f"其中两臂逐字段不同的 {len(bad)} 个"
              + ("  ⇒ 这些局两臂确实同种子同落子" if not bad else f"  ★须查 {bad[:5]}"))
    ovr = [A[s]["overridden"] for s in common]
    dec = [A[s]["train_decisions"] for s in common]
    print(f"           兜底改写次数/局：{st.mean(ovr):.2f}（训练决策 {st.mean(dec):.1f} 手/局 ⇒ "
          f"{100 * st.mean(ovr) / max(st.mean(dec), 1):.1f}% 的落子被重排改写）")

    # ---- 1. 逐场档位 ----
    schedules = sorted({int(cp[0]) for s in common for cp in A[s]["cp_log"]})
    print("\n[逐场发表会大成功率]（配对，同一局两臂）")
    print(f"{'场':<4}{'A 兜底挂回':>14}{'B 发布后':>12}{'差(B−A)':>10}{'只有A':>8}{'只有B':>8}{'McNemar p':>13}")
    for sc in schedules:
        ga = {s: any(int(c[0]) == sc and bool(c[1]) for c in A[s]["cp_log"]) for s in common}
        gb = {s: any(int(c[0]) == sc and bool(c[1]) for c in B[s]["cp_log"]) for s in common}
        seen = [s for s in common if any(int(c[0]) == sc for c in A[s]["cp_log"] + B[s]["cp_log"])]
        na, nb = len(seen), len(seen)
        ka = sum(ga[s] for s in seen)
        kb = sum(gb[s] for s in seen)
        b_only_a = sum(1 for s in seen if ga[s] and not gb[s])
        c_only_b = sum(1 for s in seen if gb[s] and not ga[s])
        p = mcnemar_exact(b_only_a, c_only_b)
        pa = 100 * ka / na if na else float("nan")
        pb = 100 * kb / nb if nb else float("nan")
        print(f"{sc:<4}{pa:>12.1f}%{pb:>11.1f}%{pb - pa:>+10.1f}{b_only_a:>8}{c_only_b:>8}{p:>13.2e}")

    # ---- 2. 三分量配对差 ----
    print("\n[三分量配对差 B−A]（正＝发布后更好）")
    for key, zh in (("five_sum", "五维和"), ("stat_score", "五维分"), ("pt", "PT")):
        diffs = [B[s][key] - A[s][key] for s in common]
        m, lo, hi, n = paired_ci(diffs)
        up = sum(1 for d in diffs if d > 0)
        dn = sum(1 for d in diffs if d < 0)
        verdict = "显著" if lo > 0 or hi < 0 else "不显著（区间含 0）"
        print(f"  {zh:<6} {m:>+9.1f} [{lo:>+9.1f}, {hi:>+9.1f}]  n={n}  更高 {up} / 更低 {dn}  ⇒ {verdict}")

    # ---- 附：达标即档位判据的落点（act_level 终值）
    print("\n[演目 Lv 终值均值]（兜底到底在推哪一格，看这个）")
    names = ["速度", "耐力", "力量", "根性", "智力"]
    la = [st.mean([A[s]["act_level"][i] for s in common]) for i in range(5)]
    lb = [st.mean([B[s]["act_level"][i] for s in common]) for i in range(5)]
    for i, nm in enumerate(names):
        print(f"  {nm:<4} A={la[i]:.2f}  B={lb[i]:.2f}  差={lb[i] - la[i]:+.2f}")


if __name__ == "__main__":
    main()
