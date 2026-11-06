#!/usr/bin/env python3
"""#90 读数：引擎稽古 **SP 那一列的链**逐层乘区（A″ 臂）对着真机 `pt_festa` 判。

背景：#89 的 D 臂把表侧 109（`sp_limit`）当加项接进 `keiko_chain`，festa 超到真机 2–3.4×；
而报文两步估计（282 手）说真机 SP 腿的总乘区只有 **≈1.1–1.2**。
两边只有一个自洽读法：**引擎在 SP 这一列上乘的层数比真机多**。
本脚本把引擎那一列拆成 `chain_in → keiko_chain 输出 → ×pt_mult` 三段直接量，
并把真机侧的「隐含乘区」按同样分桶并排打出来。

**不产出任何系数**：只打印比值，判据是「引擎 mult_total 与真机隐含乘区是否同数」。

用法（仓根）：
    python tools/sp_leg_table.py                       # A″ vs 真机
    python tools/sp_leg_table.py --engine out/legcell_d     # 换一臂（如 D 臂）
自检：A″ 应与 A2（同种子、上一版 exe）逐字节相同 ⇒ 新打点只读不写。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import statistics as st
import sys

AXES = ("速", "耐", "力", "根", "智")
BANDS = [("Lv1-3", (1, 2, 3)), ("Lv4-5", (4, 5)), ("Lv6-8", (6, 7, 8)), ("Lv9-10", (9, 10))]


def band_of(lv):
    for name, lvs in BANDS:
        if lv in lvs:
            return name
    return None


def load_engine(paths):
    agg = collections.defaultdict(lambda: [0.0] * 4)  # band → [手, Σchain_in, Σchain_out, Σfinal]
    for p in paths:
        d = json.load(open(p, encoding="utf-8"))
        n = d["n"]
        sp = d.get("keiko_sp_leg")
        if not sp:
            sys.exit(f"{p}: 没有 keiko_sp_leg 字段——插桩 exe 用错（需 #90 之后的壳）")
        for c in sp["cells"]:
            b = band_of(c["lv"])
            if b is None or c["hands"] <= c["failed_per_run"] * n:
                continue
            ok = (c["hands"] - c["failed_per_run"] * n)
            a = agg[(c["axis"], b)]
            a[0] += ok
            a[1] += c["chain_in"] * ok
            a[2] += c["chain_out"] * ok
            a[3] += c["final_pt"] * ok
    return agg


def load_real(csv_path, data_dir):
    d = json.load(open(os.path.join(data_dir, "scenario_matsuri.json"), encoding="utf-8"))
    ale = {e["level"]: (e["sp_practice_pct"], e["sp_limit"]) for e in d["act_level_effects"]}
    kb = [r[5] for r in d["keiko_basic_value"]]
    kbl = [r[5] for r in d["keiko_basic_value_late"]]
    agg = collections.defaultdict(list)
    for r in csv.DictReader(open(csv_path, encoding="utf-8-sig")):
        try:
            lv = int(float(r["act_lv"]))
            ft = float(r["pt_festa"])
            at = int(float(r["act_type"]))
            nd = int(float(r["n_deck"] or 0))
        except (TypeError, ValueError):
            continue
        b = band_of(lv)
        if b is None or not 1 <= at <= 5 or ft <= 0:
            continue
        base = (kbl if int(r["turn"]) >= 23 else kb)[at - 1]
        if base <= 0:
            continue
        pct, sl = ale[lv]
        den = base * (1 + pct / 100.0)              # ＝ 引擎 chain_in 的表侧对应物
        agg[(at - 1, b)].append((ft / den, ft / (den * (1 + 0.05 * nd))))
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="out/legcell_sp", help="A″ 产物目录（res_SPA_s*.json）")
    ap.add_argument("--real-csv", default="out/lv/_keiko_curve_perhand.csv")
    ap.add_argument("--data", default="E:/HJM/_wt_legcell/data")
    ap.add_argument("--control", default="out/legcell_ab", help="A2 产物目录，用来验打点只读")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.engine, "res_SPA_s*.json")))
    if not paths:
        sys.exit(f"没有 {args.engine}/res_SPA_s*.json")
    eng = load_engine(paths)
    real = load_real(args.real_csv, args.data)

    print("== 引擎 SP 腿逐层乘区（A″，只读打点） vs 真机 pt_festa 的隐含乘区 ==")
    print(f"{'轴':<4} {'Lv带':<7} {'引擎手/局':>9} {'chain_in':>9} {'chain_out':>9} {'final':>9} "
          f"{'链内×':>7} {'×pt_mult':>9} {'总×':>7} | 真机隐含×(不含人头项) {'含+5%/人头':>11} (n)")
    tot = collections.defaultdict(lambda: [0.0] * 3)
    for axis in range(5):
        for b, _ in BANDS:
            e = eng.get((axis, b))
            r = real.get((axis, b))
            if not e or e[0] <= 0:
                continue
            ci, co, fn = e[1] / e[0], e[2] / e[0], e[3] / e[0]
            for i, v in enumerate((ci, co, fn)):
                tot[b][i] += v * e[0]
            rr = f"{st.median([x[0] for x in r]):>20.2f} {st.median([x[1] for x in r]):>11.2f} (n{len(r)})" \
                if r else " " * 34 + "  (真机无同格)"
            print(f"{AXES[axis]:<4} {b:<7} {e[0]/150:>9.2f} {ci:>9.1f} {co:>9.1f} {fn:>9.1f} "
                  f"{co/ci if ci else 0:>7.2f} {fn/co if co else 0:>9.2f} {fn/ci if ci else 0:>7.2f} | {rr}")
    print("\n== 缺的那一维长什么样：真机隐含× ÷ 引擎总×（同轴同带，>1 ＝ 引擎在这一点乘得少）==")
    print(f"{'轴':<4}" + "".join(f"{b:>10}" for b, _ in BANDS))
    for axis in range(5):
        line = f"{AXES[axis]:<4}"
        for b, _ in BANDS:
            e = eng.get((axis, b))
            r = real.get((axis, b))
            if not e or not r or e[0] <= 0 or e[1] <= 0:
                line += f"{'—':>10}"
                continue
            line += f"{st.median([x[0] for x in r]) / (e[3] / e[1]):>10.2f}"
        print(line)
    print("\n判据（两条，各自独立）：")
    print("  ① `×pt_mult` 那一列若 ≈1.0 ⇒ SP 腿没有『链外再吃一次 effect_mult』，双吃假设作废；")
    print("  ② 上面那张比值表若**随 Lv 上升** ⇒ 真机 SP 腿带一个引擎没有的 Lv 维乘数，")
    print("     那么 #89 D 臂超发就是必然（把 109 加进 base 再乘引擎那个平的大乘区），")
    print("     要修的是『Lv 维乘数』这一维本身，不是 109 那一列的值。")


if __name__ == "__main__":
    main()
