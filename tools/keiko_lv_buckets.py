#!/usr/bin/env python3
"""稽古/演目 PT/手 按**演目 Lv** 分桶：引擎插桩 dump ↔ 真机报文曲线，同桶逐桶对。

要回答的问题只有一个：引擎的稽古率缺口（引擎 72.8 PT/手 vs 真机 121.3 PT/手，
`docs/PT缺口逐源_20261106.md` 的率项 +1,102）里，有多少落在 **Lv≥7** 那些桶。
引擎侧 `crates/matsuri-core/src/game/mod.rs` 的稽古演目腿是 `keiko_chain(row[5] + sp_bonus, 5)`，
基数只查 `keiko_basic_value` 的 8–12，**不看 `act_level`** ⇒ 稽古演目腿没有 Lv 维。
本脚本只测量、不接线、不引入任何系数。

输入
----
1. 引擎一手：插桩 `matsuri-dist`（工作树 `_wt_lv`，`patch_80_engine_window_ledger_for_measure.patch`
   ＋第 8 列 `act_lv`）的 `MC_DUMP` 分片，默认 `out/lv/dump_LV_s*.jsonl`。
   每行一局，`ptrace` = 若干 8 元组
   `[engine_turn(0基), stage, fam, pre_pt, post_pt, is_race, is_cp, act_lv]`
   —— 前 7 列顺序/含义与主仓 `tools/engine_pt_ledger.py` 依赖的完全一致，第 8 列是本测量新增。
   稽古落子窗口 = `stage == 1 and fam == 2`（合宿回合的训练在引擎里同走 `do_keiko_impl` ⇒ 族码 2），
   `act_lv` = 该手所选演目位**结算前**的 `act_level`（非稽古手恒 0）。
2. 真机一手：`tools/keiko_curve.py --csv out/lv/_keiko_curve_perhand.csv` 的逐手明细
   （248→282 手，一条 = 一次 `command_id ∈ 5101..5105` 的请求-快照配对；
   `pt_real` = 该请求前后同会话 `skill_point` 窗口差分，与引擎的落子窗口同口径 R2）。

自检（不通过就不出表）：逐局 `Σ(post−pre) == pt − 1,944`（t30 cap-10 锚点起始 PT）。

用法（仓根跑）：
    python tools/keiko_lv_buckets.py
    python tools/keiko_lv_buckets.py --dumps "out/lv/dump_LV_s*.jsonl" --real out/lv/_keiko_curve_perhand.csv
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

ANCHOR_PT = 1944          # _state_t30_cap10.json 的起始 skill_pt
# 分桶：Lv1-3 / 4 / 5 / 6 / 7-10（cap-10 锚点后 Lv7-10 才第一次打得到）
BUCKETS = [("Lv1-3", 1, 3), ("Lv4", 4, 4), ("Lv5", 5, 5), ("Lv6", 6, 6), ("Lv7-10", 7, 10)]
LABELS = [b[0] for b in BUCKETS]


def bucket_of(lv: int):
    for name, lo, hi in BUCKETS:
        if lo <= lv <= hi:
            return name
    return None                      # 越界（Lv<=0 或 >10）


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def engine_side(patterns, per_game_check):
    """引擎：逐局账平自检 + 按 Lv 桶累计稽古手数字段。"""
    files = []
    for p in patterns:
        files.extend(sorted(glob.glob(p)) if glob.has_magic(p) else [p])
    games = 0
    bad = []
    hands = collections.Counter()     # 桶 -> 正差分手数（口径同 #80）
    hands_all = collections.Counter() # 桶 -> 全部落子手数（含失败/0 差分）
    pts = collections.Counter()       # 桶 -> 稽古 PT（累计）
    lv_hist = collections.Counter()   # 桶/越界 -> 手数
    lv_hands = collections.Counter()  # 逐 Lv（1..10）正差分手数（口径同 #80）
    lv_all = collections.Counter()      # 逐 Lv 全部落子手数
    lv_pts = collections.Counter()    # 逐 Lv 稽古 PT
    keiko_total = 0
    keiko_pt_total = 0
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                tr = rec.get("ptrace")
                if tr is None:
                    sys.exit(f"{f}: 该行没有 ptrace 列——插桩 exe 用错")
                games += 1
                s = sum(r[4] - r[3] for r in tr)
                if s != rec["pt"] - ANCHOR_PT:
                    bad.append((f, rec.get("i"), s, rec["pt"] - ANCHOR_PT))
                if per_game_check:
                    per_game_check(rec, tr, games)
                for r in tr:
                    if r[1] != 1 or r[2] != 2:      # 只认落子窗口 + 稽古/演目族
                        continue
                    lv = r[7]
                    b = bucket_of(lv)
                    d = r[4] - r[3]
                    keiko_total += 1
                    keiko_pt_total += d
                    if b is not None:
                        hands_all[b] += 1          # 含失败/0 差分的手，只作对照
                    if b is None:
                        lv_hist[f"越界(Lv={lv})"] += 1
                        hands[f"越界(Lv={lv})"] += 1
                        pts[f"越界(Lv={lv})"] += d
                    else:
                        lv_hist[b] += 1
                        if d > 0:                  # 口径对齐 #80：率＝PT/正差分手
                            hands[b] += 1
                            pts[b] += d
                        lv_all[lv] += 1              # 含失败/0 差分，只作对照
                        if d > 0:                   # ④ 与 ③ 同口径：率＝PT/正差分手
                            lv_hands[lv] += 1
                            lv_pts[lv] += d
    return dict(files=files, games=games, bad=bad, hands=hands, pts=pts,
                hands_all=hands_all,
                lv_hist=lv_hist, lv_hands=lv_hands, lv_pts=lv_pts, lv_all=lv_all,
                keiko_total=keiko_total, keiko_pt_total=keiko_pt_total)


def real_side(csv_path, min_turn):
    """真机：keiko_curve.py 的逐手 CSV → 同桶的 home/演目(festa)/合计 PT/手。"""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    if min_turn is not None:
        rows = [r for r in rows if num(r["turn"]) is not None and num(r["turn"]) >= min_turn]
    by = collections.defaultdict(list)
    bylv = collections.defaultdict(list)
    oob = collections.Counter()
    for r in rows:
        lv = num(r["act_lv"])
        b = bucket_of(lv if lv is not None else -1)
        if b is None:
            oob[f"越界(Lv={lv})"] += 1
            continue
        by[b].append(r)
        bylv[lv].append(r)
    full_runs = {num(r["run"]) for r in rows}

    def agg(g):
        home = [num(r["pt_home"]) or 0 for r in g]
        festa = [num(r["pt_festa"]) or 0 for r in g]
        real = [num(r["pt_real"]) for r in g]
        real = [x for x in real if x is not None]
        return dict(n=len(g),
                    home=st.mean(home) if home else 0.0,
                    festa=st.mean(festa) if festa else 0.0,
                    total=st.mean(real) if real else 0.0,
                    sum_total=sum(real))

    return {b: agg(g) for b, g in by.items()}, oob, len(rows), full_runs, \
        {lv: agg(g) for lv, g in bylv.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", nargs="*", default=["out/lv/dump_LV_s*.jsonl"])
    ap.add_argument("--real", default="out/lv/_keiko_curve_perhand.csv")
    ap.add_argument("--real-min-turn", type=int, default=30,
                    help="真机侧同口径段界（面板回合 ≥N，默认 30；-1 = 不收窄）")
    ap.add_argument("--out", default="out/lv/keiko_lv_buckets.txt")
    args = ap.parse_args()

    lines = []

    def say(s=""):
        print(s)
        lines.append(s)

    eng = engine_side(args.dumps, None)
    say(f"== 引擎侧：{len(eng['files'])} 片 / {eng['games']} 局 ==")
    say(f"  账平自检 Σ腿差分 == PT − {ANCHOR_PT}：{eng['games'] - len(eng['bad'])}/{eng['games']} PASS")
    for b in eng["bad"][:10]:
        say(f"    FAIL {b}")
    if eng["bad"]:
        say("  !! 有局不平，下面的表不可信，先查插桩。")
    say(f"  稽古落子手合计 {eng['keiko_total']} 手 / {eng['keiko_pt_total']} PT"
        f" = {eng['keiko_pt_total'] / max(eng['keiko_total'], 1):.1f} PT/手"
        f"（{eng['keiko_total'] / max(eng['games'], 1):.1f} 手/局）")

    say()
    say("== ① 引擎分桶（按该手所在演目 Lv）==")
    say("  桶       手数/局   稽古PT/局   PT/手")
    e_hpg, e_ptpg = {}, {}
    for b in LABELS + [k for k in eng["hands"] if k not in LABELS]:
        h = eng["hands"][b] / eng["games"]
        p = eng["pts"][b] / eng["games"]
        e_hpg[b], e_ptpg[b] = h, p
        say(f"  {b:<9} {h:>7.2f}   {p:>9.1f}   {p / h if h else 0:>6.1f}")

    rmin = None if args.real_min_turn is not None and args.real_min_turn < 0 else args.real_min_turn
    real, roob, rn, rruns, reallv = real_side(args.real, rmin)
    n_runs = len([x for x in rruns if x is not None])
    say()
    say(f"== ② 真机分桶（tools/keiko_curve.py 逐手 CSV，面板回合 ≥{rmin}，一手 {rn} 手 / "
        f"{n_runs} 个 run）==")
    say("  桶        n     手数/局   home腿/手  festa(演目)腿/手  合计PT/手")
    r_hpg = {}
    for b in LABELS + [k for k in roob]:
        d = real.get(b, dict(n=roob.get(b, 0), home=0.0, festa=0.0, total=0.0, sum_total=0))
        r_hpg[b] = d["n"] / max(n_runs, 1)
        say(f"  {b:<9} {d['n']:<6} {r_hpg[b]:>8.2f}   {d['home']:>8.1f}   "
            f"{d['festa']:>10.1f}      {d['total']:>8.1f}")
    for k, v in roob.items():
        say(f"  {k:<9} {v:<6} （越界，不入分解）")

    say()
    say("== ③ 逐桶缺口分解：贡献 = (真机该桶 PT/手 − 引擎该桶 PT/手) × 引擎该桶手数/局 ==")
    say("  桶        引擎PT/手  真机PT/手    Δ率    引擎手数/局   贡献PT/局   占率项缺口")
    contrib = {}
    for b in LABELS:
        rate_e = e_ptpg[b] / e_hpg[b] if e_hpg[b] else 0.0
        rate_r = real.get(b, {}).get("total", 0.0)
        d = rate_r - rate_e
        contrib[b] = d * e_hpg[b]
    tot = sum(contrib.values())
    for b in LABELS:
        rate_e = e_ptpg[b] / e_hpg[b] if e_hpg[b] else 0.0
        rate_r = real.get(b, {}).get("total", 0.0)
        say(f"  {b:<9} {rate_e:>9.1f}  {rate_r:>9.1f}  {rate_r - rate_e:>7.1f}  "
            f"{e_hpg[b]:>12.2f}   {contrib[b]:>10.1f}   {contrib[b] / tot * 100 if tot else 0:>6.1f}%")
    say(f"  {'合计':<8} {'':>9}  {'':>9}  {'':>7}  {sum(e_hpg.values()):>12.2f}   {tot:>10.1f}")
    hi = sum(contrib[b] for b in ("Lv7-10",))
    oob_e = sum(v for k, v in eng["hands"].items() if k not in LABELS)
    say()
    say(f"  Lv≥7 桶贡献 {hi:.1f} PT/局 ＝ 整个率项缺口的 {hi / tot * 100 if tot else 0:.1f}%")
    say(f"  引擎越界桶手数（Lv≤0 或 >10）：{oob_e}（必须为 0）；真机越界：{sum(roob.values())}")

    say()
    say("== ④ 逐 Lv 形状对照（这条才直接判「引擎稽古有没有 Lv 维」）==")
    say("  Lv    引擎手数/局  引擎PT/手   真机n   真机home/手  真机festa/手  真机合计/手  引擎/真机")
    for lv in range(1, 11):
        he = eng["lv_hands"][lv] / eng["games"]
        pe = eng["lv_pts"][lv] / eng["lv_hands"][lv] if eng["lv_hands"][lv] else 0.0
        d = reallv.get(lv, dict(n=0, home=0.0, festa=0.0, total=0.0))
        ratio = pe / d["total"] if d["total"] else float("nan")
        say(f"  {lv:<5} {he:>11.2f}  {pe:>9.1f}  {d['n']:>7}  {d['home']:>11.1f}  "
            f"{d['festa']:>12.1f}  {d['total']:>11.1f}   {ratio:>6.2f}")
    e_lo = [eng["lv_pts"][l] / eng["lv_hands"][l] for l in (2, 3, 4, 5) if eng["lv_hands"][l]]
    e_hi = [eng["lv_pts"][l] / eng["lv_hands"][l] for l in (8, 9, 10) if eng["lv_hands"][l]]
    r_lo = [reallv[l]["total"] for l in (2, 3, 4, 5) if l in reallv]
    r_hi = [reallv[l]["total"] for l in (8, 9, 10) if l in reallv]
    if e_lo and e_hi and r_lo and r_hi:
        say(f"  低 Lv（2-5）→ 高 Lv（8-10）的倍率：引擎 {st.mean(e_hi) / st.mean(e_lo):.2f}×"
            f"（整手窗口）／真机 {st.mean(r_hi) / st.mean(r_lo):.2f}×")
        say(f"  真机**演目腿**同区间倍率："
            f"{st.mean([reallv[l]['festa'] for l in (8, 9, 10) if l in reallv]) / st.mean([reallv[l]['festa'] for l in (2, 3, 4, 5) if l in reallv]):.2f}×"
            "（引擎拆不出演目单腿，只能看整手）")

    say()
    say("== ⑤ 稳健性：换**真机手数**加权的同一分解（Δ率 × 真机该桶手数/局）==")
    contrib_r = {}
    for b in LABELS:
        rate_e = e_ptpg[b] / e_hpg[b] if e_hpg[b] else 0.0
        rate_r = real.get(b, {}).get("total", 0.0)
        contrib_r[b] = (rate_r - rate_e) * r_hpg.get(b, 0.0)
    tot_r = sum(contrib_r.values())
    for b in LABELS:
        say(f"  {b:<9} 贡献 {contrib_r[b]:>8.1f} PT/局   "
            f"占 {contrib_r[b] / tot_r * 100 if tot_r else 0:>5.1f}%")
    say(f"  ⇒ 真机权重下 Lv≥7 占 {contrib_r['Lv7-10'] / tot_r * 100:.1f}%"
        f"（引擎权重下 {hi / tot * 100 if tot else 0:.1f}%）——两权重同向才算数")

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        print(f"\n（→ {args.out}）")


if __name__ == "__main__":
    main()
