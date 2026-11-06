#!/usr/bin/env python3
"""#80 引擎侧**窗口账本**：把插桩 `MC_DUMP` 的 `ptrace` 列按真机同一套 R1–R8 归因，逐源对照 run170 的 t30 同口径段。

引擎一手 = **一个结算窗口**，与真机 `tools/pt_ledger.py` 的窗口口径一一对应，否则两边不可减。
插桩把一回合切成真机那样分开请求的腿（见 `stage` 码）：Begin 的「回合事件 / 比赛+赛后 / 発表会」三条腿
各自一个窗口，Train 的落子一个窗口——**不能**按整回合记账，因为引擎在比賽/発表会回合照样落子，
整回合合并会把落子 PT 记到比赛/发表会名下（实测发表会会从 1,050 虚到 1,501）。

归因规则（照抄 `tools/pt_ledger.py` 顶部 docstring 的 R1–R8，判定顺序与 `pt_ledger_segment.py::classify` 一致）：
  R7 负差分(支出) 先判 → R4 发表会(落点回合∈{24,48,68,78}) → R5 比赛(race 回合) →
  R1 训练手(cid101..106 ↔ 族码1) / R2 稽古·演目(cid5101..5105 ↔ 族码2，合宿的训练也算) / R3 外出 ↔ 族码3 →
  R6 事件(stage0 回合事件腿、stage3 的非比赛回合) → R8 其他(stage1 的休息族码4 / 保健室6；自选赛族码5 记进比赛)。
  族码由插桩给出：`crates/matsuri-core/src/game/mod.rs` 的 `pt_family`（只在测量壳里）。

输入：插桩 `matsuri-dist` 的 `MC_DUMP` 分片（jsonl，每行一局，新增列 `ptrace`）
     每行 7 元组 [engine_turn, stage, fam, pre_pt, post_pt, is_race, is_cp]
     （engine_turn 0 基，面板回合 = +1；stage 0=事件腿 3=比赛+赛后腿 4=発表会腿 5=Begin 收尾腿 1=落子 2=End）
用法：`python tools/engine_pt_ledger.py "out/ptledger/dump_s*.jsonl" [--decomp-doc out/ptledger/decomp.csv]`
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import statistics
import sys

# 真机基线：run170 的**同口径段**＝「面板落点回合 ≥ 30」的正差分窗口（一手报文，2026-11-06 上午）。
# 为什么是 ≥30 而不是 pt_ledger_segment 的 >30：引擎从 t30 锚点起跑，**第一个窗口就是面板第 30 回合
# 那一手**（锚点 pre_pt=1944，该手窗口 post_turn=30、d=+75 落在 R2 稽古），>30 会把它切掉，
# 于是两边差一个手（真机 6,411 vs 引擎含这手）。⇒ 本表 = `pt_ledger_segment` 的 R1–R8 同规则，
# 只是段界从 >30 挪到 ≥30；实测该界挪动只影响稽古那一格（24→25 手、2,957→3,032 PT）。
TRUE = {
    "稽古/演目": (25, 3032),
    "比赛": (13, 1821),
    "发表会": (3, 1050),
    "训练手": (10, 498),
    "事件": (13, 85),
}
TRUE_POS_TOTAL = 6486          # 同段 Σ正差分（= 峰值 8,430 − 锚点 1,944，逐位对上）
TRUE_GT30_TOTAL = 6411         # pt_ledger_segment --run 170 --from 30 的原读数（少一手机制见上）
TRUE_NET = 6506                # docs/命令号两族更正_20261106.md 用的「真机净积」（峰值按 8,450 读）
ANCHOR_PT = 1944               # t30 锚点起始 skill_pt
ENGINE_NET_REF = 5063          # 同文档的引擎净积（本次 300 局复算 = 5,062.9，逐位重现）
GAP = TRUE_NET - ENGINE_NET_REF  # 1443（交接给本任务的靶子）
GAP_SAME_BOUND = TRUE_POS_TOTAL - ENGINE_NET_REF  # 1423：把段界统一到 ≥30 后的缺口

FAM_SRC = {1: "训练手", 2: "稽古/演目", 3: "外出", 4: "其他命令", 5: "比赛", 6: "其他命令"}
ORDER = ["稽古/演目", "比赛", "发表会", "训练手", "事件", "外出", "其他命令", "支出", "未归因"]


def num(x):
    try:
        return int(float(x))
    except Exception:
        return None


def windows(rows):
    """一局的 ptrace 行 → **窗口序列** [(turn, 窗口号, source, delta)]（按回合、按结算顺序）。

    窗口号（插桩给的 stage 码）：0=Begin 回合事件腿 / 3=Begin 比赛+赛后腿 /
    4=Begin 発表会腿 / 5=Begin 收尾腿（恒 0，只为让账连续）/ 1=Train 落子腿 / 2=End 回合结算腿。
    """
    out = []
    for turn, stage, fam, _pre, post, race, _cp in sorted(rows):
        d = post - _pre
        if stage == 1:                                  # 落子窗口：R1/R2/R3/R8 按动作族
            src = FAM_SRC[fam] if fam else "未归因"
        elif stage == 4:
            src = "发表会"
        elif stage == 3:
            src = "比赛" if race else "事件"
        elif stage == 0:
            src = "事件"
        else:                                           # 2=End、5=Begin 收尾
            src = "其他命令"
        out.append((turn, stage, src, d))
    return out


def classify(d):
    """负差分一律走 R7（支出）——引擎没有技能购入模型，实测这条恒 0，保留判据。"""
    return "支出" if d < 0 else None


def load(paths):
    per_run = []
    for p in paths:
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            tr = rec.get("ptrace")
            if tr is None:
                sys.exit(f"{p}: 该行没有 ptrace 列——插桩 exe 用错（需要 _wt_ptledger 的 matsuri-dist）")
            ws = windows(tr)
            src = collections.defaultdict(lambda: [0, 0, 0])   # [n手(正差分窗口), PT, 全部窗口数]
            gross = spend = 0
            for _t, _st, s, d in ws:
                s = classify(d) or s
                if d > 0:
                    gross += d
                    src[s][0] += 1
                    src[s][1] += d
                elif d < 0:
                    spend += d
                src[s][2] += 1
            net = gross + spend
            per_run.append({"src": src, "gross": gross, "spend": spend,
                            "final_pt": rec.get("pt"), "net": net})
    return per_run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dumps", nargs="*")
    ap.add_argument("--decomp-doc", default=None, help="把三项分解写成 csv 的路径（可选）")
    args = ap.parse_args()
    paths = []
    for g in args.dumps:
        paths.extend(sorted(glob.glob(g)) if any(c in g for c in "*?[") else [g])
    if not paths:
        sys.exit("给至少一个 MC_DUMP 分片路径")
    runs = load(paths)
    n = len(runs)

    agg = collections.defaultdict(lambda: [0, 0, 0])
    for r in runs:
        for s, v in r["src"].items():
            for i in range(3):
                agg[s][i] += v[i]
    gross = [r["gross"] for r in runs]
    spend = [r["spend"] for r in runs]
    finals = [r["final_pt"] for r in runs]
    net = [g + s for g, s in zip(gross, spend)]

    print(f"=== 引擎窗口账本 n={n} 局（插桩 matsuri-dist，MC_POLICY=search MC_K=64，t30 cap-10 锚点）===")
    print(f"终值 PT  mean={statistics.mean(finals):.1f}  净值 mean={statistics.mean(net):.1f}"
          f"  支出 mean={statistics.mean(spend):.1f}")
    print(f"引擎净值 vs 文档 5,063 → 差 {statistics.mean(net) - ENGINE_NET_REF:+.1f}")
    print(f"自检：Σ窗口差分 == 终值 − 锚点？ "
          f"{'PASS' if all(r['final_pt'] - ANCHOR_PT == r['gross'] + r['spend'] for r in runs) else 'FAIL'}")
    print()
    print(f"{'source':<12}{'真机n手':>8}{'真机PT':>9}{'真机率':>9}"
          f"{'引擎n手':>10}{'引擎PT':>10}{'引擎率':>9}{'缺口PT':>10}{'引擎窗口':>10}")
    rows = []
    for s in ORDER:
        tn, tp = TRUE.get(s, (0, 0))
        en, ep, ew = agg[s][0] / n, agg[s][1] / n, agg[s][2] / n
        tr_ = tp / tn if tn else 0.0
        er_ = ep / en if en else 0.0
        rows.append((s, tn, tp, tr_, en, ep, er_))
        print(f"{s:<12}{tn:>8}{tp:>9}{tr_:>9.1f}{en:>10.2f}{ep:>10.1f}{er_:>9.1f}"
              f"{tp - ep:>10.1f}{ew:>10.2f}")
    print(f"{'合计(正差分)':<12}{sum(t[1] for t in rows):>8}{TRUE_POS_TOTAL:>9}{'':>9}"
          f"{sum(r[4] for r in rows):>10.2f}{sum(r[5] for r in rows):>10.1f}")

    print("\n=== 乘积分解（缺口按 真机 − 引擎 记；手＝引擎手数、率＝单手 PT）===")
    print("  两项切法（本仓 #40 家法，恒等于该源缺口）：")
    print("    手数项 = (真机手数 − 引擎手数) × 真机单手率")
    print("    率项   = 引擎手数 × (真机单手率 − 引擎单手率)")
    print("  三项切法（把交叉项单列，供对照）：Δ手数×真机率 + 真机手数×Δ率 + Δ手数×Δ率")
    print(f"{'source':<12}{'缺口PT':>10}{'Δ手数':>9}{'Δ单手率':>10}"
          f"{'手数项':>10}{'率项':>10}{'三项:手数':>10}{'三项:率':>10}{'三项:交叉':>10}{'三项和':>10}")
    csv_rows = []
    two = [0.0, 0.0]
    three = [0.0, 0.0, 0.0]
    gapsum = 0.0
    for s, tn, tp, tr_, en, ep, er_ in rows:
        gap = tp - ep                      # 真机 − 引擎（>0 = 引擎欠产）
        gapsum += gap
        dn = tn - en                       # 真机比引擎多几手
        dr = tr_ - er_
        # 两项切法
        a2, b2 = dn * tr_, en * dr
        # 三项切法（率项按真机手数计，交叉项 = Δn×Δrate）
        a3, b3, c3 = dn * tr_, tn * dr, -dn * dr
        if tn and en:
            print(f"{s:<12}{gap:>10.1f}{dn:>9.2f}{dr:>10.1f}{a2:>10.1f}{b2:>10.1f}"
                  f"{a3:>10.1f}{b3:>10.1f}{c3:>10.1f}{a3 + b3 + c3:>10.1f}")
        elif tn:                            # 引擎该源 0 手 ⇒ 全落手数项
            print(f"{s:<12}{gap:>10.1f}{dn:>9.2f}{'—':>10}{a2:>10.1f}{'—':>10}"
                  f"{a3:>10.1f}{b3:>10.1f}{c3:>10.1f}{a3 + b3 + c3:>10.1f}")
        else:                               # 真机该源 0 手 ⇒ 无法乘积分解，整笔按 ΔPT 记（非残差项）
            print(f"{s:<12}{gap:>10.1f}{dn:>9.2f}{'—':>10}{'—':>10}{'—':>10}"
                  f"{'—':>10}{'—':>10}{'—':>10}{'—':>10}  (真机 n手=0，无单手率可乘，ΔPT 整笔入账)")
        two[0] += a2; two[1] += b2
        three[0] += a3; three[1] += b3; three[2] += c3
        csv_rows.append([s, tn, tp, round(tr_, 2), round(en, 2), round(ep, 1), round(er_, 2),
                         round(gap, 1), round(dn, 3), round(dr, 2),
                         round(a2, 1), round(b2, 1), round(a3, 1), round(b3, 1), round(c3, 1)])
    print(f"\nΣ两项切法：手数项 {two[0]:+.1f} ＋ 率项 {two[1]:+.1f} = {sum(two):+.1f}")
    print(f"Σ三项切法：手数项 {three[0]:+.1f} ＋ 率项 {three[1]:+.1f} ＋ 交叉项 {three[2]:+.1f} = {sum(three):+.1f}")
    print(f"Σ逐源缺口（真机 − 引擎，正差入口径）= {gapsum:+.1f}")

    print(f"\n=== 闭合检查 ===")
    for label, target in ((f"靶子 {GAP}（净值口径，文档原数）", GAP),
                          (f"同界缺口 {GAP_SAME_BOUND}（真机 ≥30 正差分 − 引擎）", GAP_SAME_BOUND)):
        d = sum(two) - target
        print(f"vs {label:<44} 分解合计 {sum(two):+9.1f} → 差 {d:+7.1f}（{d / target * 100:+6.1f}%）"
              f"{'  PASS(±5%)' if abs(d) <= 0.05 * target else '  超 ±5%'}")
    print(f"口径注：靶子 1,443 用「真机净积 6,506 = 峰值 8,450 − 锚点 1,944」；报文窗口里 run170 的峰值 PT "
          f"实为 8,430，8,430 − {ANCHOR_PT} = {TRUE_POS_TOTAL} 与本表 Σ正差分逐位对上。"
          f"⇒ 1,443 − {GAP_SAME_BOUND} = {GAP - GAP_SAME_BOUND} 全是**峰值读数差**（8,450 vs 报文的 8,430），"
          f"与归因规则无关。另有 {TRUE_POS_TOTAL - TRUE_GT30_TOTAL} 是段界之差"
          f"（`pt_ledger_segment --from 30` 的 >30 vs 本表的 ≥30）：那 75 PT 是面板第 30 回合真实落的一手稽古，"
          f"引擎侧同样跑了这一手 ⇒ 本表按 ≥30 取。引擎侧净值 = Σ正差分（支出 0：没有技能购入模型）；"
          f"真机同段支出 −8,429 全发生在峰值之后，不进本表。")
    if args.decomp_doc:
        with open(args.decomp_doc, "w", encoding="utf-8", newline="") as f:
            import csv as _csv
            w = _csv.writer(f)
            w.writerow(["source", "true_n", "true_pt", "true_rate", "eng_n", "eng_pt", "eng_rate",
                        "gap_pt", "d_n", "d_rate", "2term_n", "2term_rate",
                        "3term_n", "3term_rate", "3term_cross"])
            w.writerows(csv_rows)


if __name__ == "__main__":
    main()
