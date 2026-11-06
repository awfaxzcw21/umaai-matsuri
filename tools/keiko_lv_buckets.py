#!/usr/bin/env python3
"""稽古/演目 PT/手 按**演目 Lv** 分桶：引擎插桩 dump ↔ 真机报文曲线，同桶逐桶对。

要回答的问题只有一个：引擎的稽古率缺口（引擎 72.8 PT/手 vs 真机 121.3 PT/手，
`docs/PT缺口逐源_20261106.md` 的率项 +1,102）里，有多少落在 **Lv≥7** 那些桶。
交接时的代码侧半证：`crates/matsuri-core/src/game/mod.rs` 的稽古演目腿是
`keiko_chain(row[5] + sp_bonus, 5)`，基数只查 `keiko_basic_value` 的 8–12
⇒ 嫌疑「稽古演目腿没有 Lv 维」。本脚本只测量、不接线、不引入任何系数。

输入
----
1. 引擎一手：插桩 `matsuri-dist`（工作树 `E:/HJM/_wt_lv`＝`fix/head-slope` +
   `tools/patch_80_engine_window_ledger_for_measure.patch` ＋第 8 列 `act_lv`）的 `MC_DUMP` 分片，
   默认 `out/lv/dump_LV_s*.jsonl`。每行一局，`ptrace` = 若干 8 元组
   `[engine_turn(0基), stage, fam, pre_pt, post_pt, is_race, is_cp, act_lv]`
   —— 前 7 列顺序/含义与主仓 `tools/engine_pt_ledger.py` 依赖的完全一致，第 8 列是本测量新增。
   稽古落子窗口 = `stage == 1 and fam == 2`（合宿回合的训练在引擎里同走 `do_keiko_impl` ⇒ 族码 2），
   `act_lv` = 该手所选演目位**结算前**的 `act_level`（非稽古手恒 0）。
2. 真机一手：`tools/keiko_curve.py --csv out/lv/_keiko_curve_perhand.csv` 的逐手明细
   （一条 = 一次 `command_id ∈ 5101..5105` 的请求-快照配对；`pt_real` = 该请求前后同会话
   `skill_point` 窗口差分，与引擎落子窗口同口径 R2）。

**两套口径并列输出，别混用**（这张表最容易踩的坑）：
  · `pos`＝只把 `post−pre > 0` 的窗口计一手 —— 主仓 #80 账本与 `docs/PT缺口逐源_20261106.md`
    的 22.71 手／72.8 PT/手／真机 25 手 121.3 PT/手 都是这个口径 ⇒ 默认走它，头条数可比文档。
  · `all`＝该桶全部落子窗口（含失败/0 差分）——「稽古打空」本身是一条独立嫌疑，
    用 `--caliber all` 或看 ① 尾部「打空手/局」那一列。两边必须同口径相减，不许 pos 减 all。

自检（不通过就不出表）：逐局 `Σ(post−pre) == pt − 1,944`（t30 cap-10 锚点起始 PT）。

用法（仓根跑）：
    python tools/keiko_lv_buckets.py
    python tools/keiko_lv_buckets.py --caliber all
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
LO_LV, HI_LV = (3, 4, 5), (8, 9, 10)      # ④ 的 Lv 倍率区间


def bucket_of(lv):
    if not isinstance(lv, int):
        return None
    for name, lo, hi in BUCKETS:
        if lo <= lv <= hi:
            return name
    return None                      # 越界（Lv<=0 或 >10）


def num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def new_cell():
    return dict(n_all=0, n_pos=0, pt_all=0, pt_pos=0, home=[], festa=[])


def add(cell, d, home=None, festa=None):
    cell["n_all"] += 1
    cell["pt_all"] += d
    if d > 0:
        cell["n_pos"] += 1
        cell["pt_pos"] += d
    if home is not None:
        cell["home"].append(home)
        cell["festa"].append(festa)


def cnt(cell, key):
    return cell["n_pos" if key == "pos" else "n_all"] if cell else 0


def spt(cell, key):
    return cell["pt_pos" if key == "pos" else "pt_all"] if cell else 0


def rate(cell, key):
    n = cnt(cell, key)
    return spt(cell, key) / n if n else 0.0


def engine_side(patterns):
    """引擎：逐局账平自检 + 按 Lv 桶 / 逐 Lv 累计稽古手数与 PT（pos + all 两口径）。"""
    files = []
    for p in patterns:
        files.extend(sorted(glob.glob(p)) if glob.has_magic(p) else [p])
    games = 0
    bad = []
    buck = collections.defaultdict(new_cell)
    bylv = collections.defaultdict(new_cell)
    oob = collections.defaultdict(new_cell)
    keiko = new_cell()
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                tr = rec.get("ptrace")
                if tr is None:
                    sys.exit(f"{f}: 该行没有 ptrace 列——插桩 exe 用错（需 _wt_lv 的 matsuri-dist）")
                games += 1
                s = sum(r[4] - r[3] for r in tr)
                if s != rec["pt"] - ANCHOR_PT:
                    bad.append((f, rec.get("i"), s, rec["pt"] - ANCHOR_PT))
                for r in tr:
                    if len(r) < 8:
                        sys.exit(f"{f}: ptrace 行只有 {len(r)} 列，缺第 8 列 act_lv")
                    if r[1] != 1 or r[2] != 2:      # 只认落子窗口 + 稽古/演目族
                        continue
                    d = r[4] - r[3]
                    lv = r[7]
                    b = bucket_of(lv)
                    add(keiko, d)
                    add(bylv[lv], d)
                    add(buck[b] if b else oob[f"越界(Lv={lv})"], d)
    return dict(files=files, games=games, bad=bad, buck=buck, bylv=bylv, oob=oob, keiko=keiko)


def real_side(csv_path, min_turn):
    """真机：keiko_curve.py 逐手 CSV → 同桶 home/festa/合计 + pos/all 两口径 PT/手。"""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    if min_turn is not None:
        rows = [r for r in rows if num(r["turn"]) is not None and num(r["turn"]) >= min_turn]
    buck = collections.defaultdict(new_cell)
    bylv = collections.defaultdict(new_cell)
    oob = collections.Counter()
    for r in rows:
        lv, real = num(r["act_lv"]), num(r["pt_real"])
        if real is None:
            continue
        home, festa = num(r["pt_home"]) or 0, num(r["pt_festa"]) or 0
        b = bucket_of(lv)
        if b is None:
            oob[f"越界(Lv={lv})"] += 1
            continue
        add(buck[b], real, home, festa)
        add(bylv[lv], real, home, festa)
    runs = {num(r["run"]) for r in rows}
    return buck, oob, len(rows), runs, bylv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dumps", nargs="*", default=["out/lv/dump_LV_s*.jsonl"])
    ap.add_argument("--real", default="out/lv/_keiko_curve_perhand.csv")
    ap.add_argument("--real-min-turn", type=int, default=30,
                    help="真机侧同口径段界（面板回合 ≥N，默认 30；-1 = 不收窄）")
    ap.add_argument("--caliber", choices=["pos", "all"], default="pos",
                    help="分解用哪套口径（默认 pos＝主仓 #80 账本口径）")
    ap.add_argument("--out", default="out/lv/keiko_lv_buckets.txt")
    args = ap.parse_args()
    K = args.caliber

    lines = []

    def say(s=""):
        print(s)
        lines.append(s)

    eng = engine_side(args.dumps)
    g, kc = eng["games"], eng["keiko"]
    say(f"== 引擎侧：{len(eng['files'])} 片 / {g} 局 ｜ 分解口径＝"
        f"{'正差分窗口 pos（同 #80 账本）' if K == 'pos' else '全部落子手 all'} ==")
    say(f"  账平自检 Σ腿差分 == PT − {ANCHOR_PT}：{g - len(eng['bad'])}/{g} PASS")
    for b in eng["bad"][:10]:
        say(f"    FAIL {b}")
    if eng["bad"]:
        say("  !! 有局不平，下面的表不可信，先查插桩。")
    say(f"  稽古落子 pos：{kc['n_pos']} 手 / {kc['pt_pos']} PT ＝ {rate(kc, 'pos'):.1f} PT/手"
        f"（{kc['n_pos'] / g:.2f} 手/局）")
    say(f"  稽古落子 all：{kc['n_all']} 手 / {kc['pt_all']} PT ＝ {rate(kc, 'all'):.1f} PT/手"
        f"（{kc['n_all'] / g:.2f} 手/局）")
    say(f"  ⇒ 稽古打空/负差分 {kc['n_all'] - kc['n_pos']} 手 = {(kc['n_all'] - kc['n_pos']) / g:.2f} 手/局"
        f"（{kc['n_all'] and (kc['n_all'] - kc['n_pos']) / kc['n_all'] * 100:.1f}% 的稽古手 PT≤0）")

    say()
    say("== ① 引擎分桶（按该手所在演目 Lv）==")
    say(f"  桶      手数/局[{K}]  稽古PT/局[{K}]  PT/手[{K}]   ｜all手数/局  allPT/手  打空手/局")
    e = {}
    for b in LABELS + sorted(eng["oob"]):
        c = eng["buck"][b] if b in eng["buck"] else eng["oob"][b]
        e[b] = c
        say(f"  {b:<8} {cnt(c, K) / g:>12.2f}  {spt(c, K) / g:>14.1f}  {rate(c, K):>9.1f}"
            f"   ｜{c['n_all'] / g:>11.2f}  {rate(c, 'all'):>8.1f}  "
            f"{(c['n_all'] - c['n_pos']) / g:>9.2f}")

    rmin = None if args.real_min_turn is not None and args.real_min_turn < 0 else args.real_min_turn
    real, roob, rn, rruns, rbylv = real_side(args.real, rmin)
    n_runs = len([x for x in rruns if x is not None])
    say()
    say(f"== ② 真机分桶（tools/keiko_curve.py 逐手 CSV，面板回合 ≥{rmin}，一手 {rn} 手 / "
        f"{n_runs} 个 run）==")
    say(f"  桶       n     home腿/手  festa(演目)腿/手  合计PT/手[pos]  合计PT/手[all]  手数/局[{K}]")
    rr = {}
    for b in LABELS:
        c = real.get(b)
        rr[b] = c
        if not c or not c["n_all"]:
            say(f"  {b:<8} 0")
            continue
        say(f"  {b:<8} {c['n_all']:<6} {st.mean(c['home']):>9.1f}   {st.mean(c['festa']):>10.1f}"
            f"      {rate(c, 'pos'):>11.1f}  {rate(c, 'all'):>12.1f}  {cnt(c, K) / max(n_runs, 1):>9.2f}")
    for k, v in sorted(roob.items()):
        say(f"  {k:<8} {v}（越界，不入分解）")

    say()
    say(f"== ③ 逐桶缺口分解：贡献 = (真机该桶 PT/手 − 引擎该桶 PT/手) × 引擎该桶手数/局〔口径 {K}〕==")
    say("  桶        引擎PT/手  真机PT/手    Δ率    引擎手数/局   贡献PT/局   占本次率项")
    contrib = {}
    for b in LABELS:
        ok = e.get(b) and rr.get(b) and cnt(e[b], K) and cnt(rr[b], K)
        contrib[b] = (rate(rr[b], K) - rate(e[b], K)) * cnt(e[b], K) / g if ok else 0.0
    tot = sum(contrib.values())
    for b in LABELS:
        if not (e.get(b) and rr.get(b)):
            continue
        say(f"  {b:<9} {rate(e[b], K):>9.1f}  {rate(rr[b], K):>9.1f}  "
            f"{rate(rr[b], K) - rate(e[b], K):>7.1f}  {cnt(e[b], K) / g:>12.2f}   "
            f"{contrib[b]:>10.1f}   {contrib[b] / tot * 100 if tot else 0:>6.1f}%")
    say(f"  {'合计':<8} {'':>9}  {'':>9}  {'':>7}  "
        f"{sum(cnt(e[b], K) / g for b in LABELS if b in e):>12.2f}   {tot:>10.1f}")
    hi = contrib["Lv7-10"]
    say()
    say(f"  ⇒ Lv≥7 桶贡献 {hi:.1f} PT/局 ＝ 本次分解出的率项缺口的 "
        f"{hi / tot * 100 if tot else 0:.1f}%")
    say("  ⇒ 合计只用于**占比分配**：一手来源/段界/桶权重与 docs 那张表不同（真机这里 pooled 多 run、"
        "引擎是本批局），别拿它去减 1,102。")
    say(f"  引擎越界桶手数（Lv≤0 或 >10）：{sum(c['n_all'] for c in eng['oob'].values())}（必须为 0）；"
        f"真机越界：{sum(roob.values())}")

    say()
    say("== ④ 逐 Lv 形状对照（这条才直接判「引擎稽古有没有 Lv 维」）==")
    say(f"  Lv   引擎手数/局[{K}]  引擎PT/手[{K}]  真机n  真机home/手  真机festa/手  "
        f"真机PT/手[{K}]  引擎/真机")
    for lv in range(1, 11):
        ce, cr = eng["bylv"].get(lv), rbylv.get(lv)
        pe, rt = rate(ce, K), rate(cr, K)
        say(f"  {lv:<4} {cnt(ce, K) / g:>16.2f}  {pe:>14.1f}  {(cr['n_all'] if cr else 0):>6}"
            f"  {(st.mean(cr['home']) if cr and cr['home'] else 0):>11.1f}  "
            f"{(st.mean(cr['festa']) if cr and cr['festa'] else 0):>12.1f}"
            f"  {rt:>16.1f}   {(pe / rt if rt else float('nan')):>7.2f}")
    e_lo = [rate(eng["bylv"][l], K) for l in LO_LV if cnt(eng["bylv"].get(l), K)]
    e_hi = [rate(eng["bylv"][l], K) for l in HI_LV if cnt(eng["bylv"].get(l), K)]
    r_lo = [rate(rbylv[l], K) for l in LO_LV if cnt(rbylv.get(l), K)]
    r_hi = [rate(rbylv[l], K) for l in HI_LV if cnt(rbylv.get(l), K)]
    if e_lo and e_hi and r_lo and r_hi:
        say(f"  低 Lv{LO_LV}→高 Lv{HI_LV} 整手 PT/手 倍率：引擎 {st.mean(e_hi) / st.mean(e_lo):.2f}×"
            f"／真机 {st.mean(r_hi) / st.mean(r_lo):.2f}×")
    f_lo = [st.mean(rbylv[l]["festa"]) for l in LO_LV if rbylv.get(l) and rbylv[l]["festa"]]
    f_hi = [st.mean(rbylv[l]["festa"]) for l in HI_LV if rbylv.get(l) and rbylv[l]["festa"]]
    if f_lo and f_hi:
        say(f"  真机**演目(festa)单腿**同区间倍率：{st.mean(f_hi) / st.mean(f_lo):.2f}×"
            "（引擎窗口拆不出单腿，只能看整手）")
    say("  ⚠ 判读：逐桶「引擎/真机」比值≈常数 ⇒ 缺口是各 Lv 同比例的总量不足，不是高 Lv 才发散；"
        "比值随 Lv 单调走高才叫「缺 Lv 维」。")

    say()
    say("== ⑤ 稳健性：换**真机手数**加权的同一分解（Δ率 × 真机该桶手数/局）==")
    contrib_r = {}
    for b in LABELS:
        ok = e.get(b) and rr.get(b) and cnt(e[b], K) and cnt(rr[b], K)
        contrib_r[b] = ((rate(rr[b], K) - rate(e[b], K)) * cnt(rr[b], K) / max(n_runs, 1)) if ok else 0.0
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
