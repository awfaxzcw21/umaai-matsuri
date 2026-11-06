#!/usr/bin/env python3
"""#92③：把「SP ÷ festa腿收益」的 (轴 × 演目Lv) 比值表固化下来。

一张表同时放三列，专门用来回答两问：
  · 表侧 201 该给的 ladder（预期，纯查表，无拟合）
  · 引擎实测（来自测量壳 `keiko_probe`）
  · 真机实测（来自 `tools/festa_sp_table.py` 的逐手表）

分母一律只用**没被 1200 软上限砍**的手（引擎＝套帽前后相等；真机＝当前值＋收益 ≤ 1200），
否则高 Lv 的比值会被分母压缩虚高（这条是 §30 收回 §29 时说清楚的那件事）。

    python tools/keiko_sp_ratio_table.py \\
        --real out/festa_sp_hands.jsonl \\
        --probe "E:/HJM/_wt_cp98/out/cp98/probe2_base_s*.jsonl" \\
        --engine-table data/scenario_matsuri.json
"""
import argparse
import collections
import glob
import json
import statistics as st

AX = ["速度", "耐力", "力量", "根性", "智力"]


def q(vals, p):
    if not vals:
        return float("nan")
    v = sorted(vals)
    return v[max(0, min(len(v) - 1, int(p * len(v))))]


def load_real(path):
    out = collections.defaultdict(list)
    tot = collections.defaultdict(list)
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        h = json.loads(line)
        gain, sp, lv, val = h.get("tt_axis"), h.get("tt30"), h.get("act_lv"), h.get("axis_val")
        if not gain or not sp or not lv:
            continue
        clipped = val is not None and val + gain > 1200
        r = sp / gain
        tot[lv].append(r)
        if not clipped:
            out[(h["axis_name"], lv)].append(r)
    return out, tot


def load_engine(pattern):
    out = collections.defaultdict(list)
    tot = collections.defaultdict(list)
    for f in glob.glob(pattern):
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            r = json.loads(line)
            for row in (r.get("keiko_probe") or []):
                idx, lv, disp, sp, failed, pre, _cur = row
                if failed or not disp or sp is None:
                    continue
                val = sp / disp
                tot[lv].append(val)
                if pre == disp:                      # 软上限没动 ⇒ 分母未被砍
                    out[(AX[idx] if idx < 5 else "?", lv)].append(val)
    return out, tot


def expected_ladder(engine_table):
    d = json.load(open(engine_table, encoding="utf-8"))
    p = {e["level"]: e["sp_practice_pct"] for e in d["act_level_effects"]}
    return p


def band(d, ax, los, his, min_n):
    lo = [x for lv in los for x in d[(ax, lv)]]
    hi = [x for lv in his for x in d[(ax, lv)]]
    if len(lo) < min_n or len(hi) < min_n:
        return None
    return q(lo, 0.5), q(hi, 0.5), len(lo), len(hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", default="out/festa_sp_hands.jsonl")
    ap.add_argument("--probe", default="E:/HJM/_wt_cp98/out/cp98/probe2_base_s*.jsonl")
    ap.add_argument("--engine-table", default="data/scenario_matsuri.json")
    ap.add_argument("--min-real", type=int, default=20)
    ap.add_argument("--min-engine", type=int, default=200)
    a = ap.parse_args()

    real, real_all = load_real(a.real)
    eng, eng_all = load_engine(a.probe)
    p = expected_ladder(a.engine_table)

    print("=== (轴 × 演目Lv) 的 SP/festa腿收益 中位（只取分母未砍的手；括号=手数）")
    print(f"{'轴':<5}" + "".join(f"{('L' + str(lv)):>18}" for lv in range(1, 11)))
    for ax in AX:
        cells = []
        for lv in range(1, 11):
            rv = real[(ax, lv)]
            ev = eng[(ax, lv)]
            rs = f"{q(rv,.5):.2f}({len(rv)})" if rv else "–"
            es = f"{q(ev,.5):.2f}({len(ev)})" if ev else "–"
            cells.append(f"{rs + '/' + es:>18}")
        print(f"{ax:<5}" + "".join(cells))
    print("  每格＝真机/引擎")

    lv_lo, lv_hi = 1, 6
    exp = (1 + p[lv_hi] / 100) / (1 + p[lv_lo] / 100)
    print("\n=== 两问的判据（低段 Lv1-2 → 高段 Lv5-7）")
    print(f"{'轴':<5}{'真机涨':>10}{'引擎涨':>10}{'表侧该涨L1→L6':>16}{'真/引':>8}")
    for ax in AX:
        rb = band(real, ax, (1, 2), (5, 6, 7), a.min_real)
        eb = band(eng, ax, (1, 2), (5, 6, 7), a.min_engine)
        if not rb or not eb:
            print(f"{ax:<5}  样本不够（真 {0 if not rb else rb[2]}／引 {0 if not eb else eb[2]}）")
            continue
        rg, eg = rb[1] / rb[0], eb[1] / eb[0]
        print(f"{ax:<5}{rg:>10.2f}{eg:>10.2f}{exp:>16.2f}{rg / eg:>8.2f}")

    lv_lo, lv_hi = 1, 6
    exp = (1 + p[lv_hi] / 100) / (1 + p[lv_lo] / 100)
    print(f"\n  表侧 201（sp_practice_pct {p[lv_lo]}% → {p[lv_hi]}%）在 L{lv_lo}→L{lv_hi} 应涨 "
          f"×{exp:.2f}  ← 这一列是**纯查表**，不含任何拟合")
    print("  对照上面『引擎涨』那一列：≈同值 ⇒ 问① 成立（引擎走了这条 ladder）；明显偏小 ⇒ 问① 才有问题。")

    print("\n=== 逐 Lv 全体（不分轴，看形状）")
    print(f"{'Lv':>3}{'真机n':>8}{'真机中位':>10} | {'引擎n':>9}{'引擎中位':>10} | {'真/引':>7}")
    for lv in range(1, 11):
        rv, ev = real_all[lv], eng_all[lv]
        print(f"{lv:>3}{len(rv):>8}{q(rv,.5):>10.3f} | {len(ev):>9}{q(ev,.5):>10.3f} | "
              f"{(q(rv,.5)/q(ev,.5) if ev and q(ev,.5) else float('nan')):>7.2f}")
    print("\n  ⚠ 口径警告：tt30 与分母那列收益出自同一块报文的同一层预告，")
    print("    所以本表描述的是客户端打印的两个数之间的关系，不证明谁生成谁；")
    print("    高 Lv 的手天然是后半场（干劲/人头/合宿不同），这些项没控住。")
    print("    禁令：不许为压平这张表引入任何 Lv 系数或拟合常数。")


if __name__ == "__main__":
    main()
