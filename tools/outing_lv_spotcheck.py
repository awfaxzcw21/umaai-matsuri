#!/usr/bin/env python3
"""#58 复核：不给模型打分，只用**自己的解码**重跑 `out/_outing_act_level_report.txt` §5 的两条主张。

要证的只有两件事（都是隔壁那份报告用来判 `mod.rs:387` 随机 Lv「虚高」的承重证据）：

  C1 一次**友人外出**只动 **1 格** act_level（报告 N=33，全 1 格）
  C2 动的那一格 = 板帧上的 `friend_outing_gain_act_level` 指针格（报告「指针=argmin，违例 0」）

与隔壁的差别：那份脚本自己做了签名归因、run 拆流、污染剔除；本脚本**不做归因**，
只把它的 (板帧, 落地帧) 名单当输入，逐帧解 `data.festa_data_set.act_info_array`
原样比 `level` / `act_gauge` / 指针 —— 判「它的明细行是不是真写得出来」。
名单不符、ΔLv ≠ 1、指针 ≠ 落格，都单独计数并打全行，不合并成一句话。

用法：
    python tools/outing_lv_spotcheck.py                       # 默认读 out/_outing_act_level_turns.csv
    python tools/outing_lv_spotcheck.py --sign 友人外出
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
CSV = "out/_outing_act_level_turns.csv"


def board(path):
    """解一帧 → ({act_type: level}, {act_type: gauge}, {被 F 标记的 act_type})。缺板返回 None。"""
    d = ml.decode_file(os.path.join(RAW, path))
    fds = (d.get("data") or {}).get("festa_data_set")
    if not isinstance(fds, dict):
        return None
    ai = fds.get("act_info_array")
    if not isinstance(ai, list) or not ai:
        return None
    lv, gg, fl = {}, {}, set()
    for e in ai:
        t = e.get("act_type")
        lv[t] = e.get("level")
        gg[t] = e.get("act_gauge")
        if e.get("friend_outing_gain_act_level"):
            fl.add(t)
    turn = (d.get("data") or {}).get("chara_info", {}).get("turn")
    return lv, gg, fl, turn


def argmin_cell(lv, gg):
    """引擎 `bump_lowest_act_level` 的形状：argmin(Lv, 槽, 下标升序)，不排除满槽。"""
    best = min(lv, key=lambda t: (lv[t], gg.get(t, 0), t))
    return best


def scan_all():
    """全量逐帧（不做任何 run 归因）：F 指针形状 ∈ {0,1} 格？F 格 = argmin(Lv,槽,下标)？
    这是 Q-B 主判据的**独立重算**——同帧内可判，不受隔壁那套签名归因/拆流影响。"""
    paths = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=lambda p: os.path.basename(p))
    n_board = n_f0 = n_f1 = n_fmore = 0
    n_agree = n_disagree = n_nocompare = 0
    dis, shape = [], collections.Counter()
    lv_max = collections.defaultdict(int)     # 逐演目格看实测天花板（判「7 级锁」那类二手说法）
    lv_n = collections.Counter()
    for p in paths:
        try:
            b = board(os.path.basename(p))
        except Exception:  # noqa: BLE001
            continue
        if b is None:
            continue
        lv, gg, fl = b[0], b[1], b[2]
        n_board += 1
        shape[len(lv)] += 1
        for t, v in lv.items():
            if isinstance(v, int):
                lv_max[t] = max(lv_max[t], v)
                lv_n[t] += 1
        if len(fl) == 0:
            n_f0 += 1
            continue
        if len(fl) == 1:
            n_f1 += 1
        else:
            n_fmore += 1
            dis.append(("F>1格", os.path.basename(p), sorted(fl)))
            continue
        if None in lv.values() or None in gg.values():
            n_nocompare += 1
            continue
        c = argmin_cell(lv, gg)
        if fl == {c}:
            n_agree += 1
        else:
            n_disagree += 1
            if len(dis) < 25:
                dis.append(("F≠argmin", os.path.basename(p),
                            f"F={sorted(fl)}", f"argmin={c}",
                            "Lv=" + "|".join(f"{t}:{lv[t]}" for t in sorted(lv)),
                            "槽=" + "|".join(f"{t}:{gg.get(t)}" for t in sorted(lv))))
    print("== Q-B 主判据独立重算（全量逐帧，无 run 归因）==")
    print(f"  带演目板的报文 : {n_board}   板格数直方 {dict(shape)}")
    print(f"  F 标记 0 格    : {n_f0}   → 该局该帧无友人外出在场，不可比")
    print(f"  F 标记 1 格    : {n_f1}   → **指针不是计数器**")
    print(f"  F 标记 >1 格   : {n_fmore}")
    print(f"  F == argmin(Lv,槽,下标) : 支持 {n_agree} / 违例 {n_disagree} / 不可比 {n_nocompare}")
    print("  逐格实测 Lv 天花板（判「某格锁在 7 级」这类二手说法；act_type↔五维 未证，故只按格报）：")
    for t in sorted(lv_max):
        print(f"    act{t}  观测 max={lv_max[t]}  帧数={lv_n[t]}")
    for d in dis:
        print("    ", d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV)
    ap.add_argument("--sign", default="友人外出")
    ap.add_argument("--scan", action="store_true", help="改为全量逐帧重算 Q-B 主判据")
    args = ap.parse_args()
    if args.scan:
        scan_all()
        return

    rows = list(csv.DictReader(open(args.csv, encoding="utf-8-sig", newline="")))
    sel = [r for r in rows if r["签名"] == args.sign and r["板帧"] and r["落地文件"]]
    print(f"签名={args.sign} 的明细行 N={len(sel)} / 全表 {len(rows)} 行")

    n_1cell = n_other = n_ptr_match = n_ptr_miss = n_fail = 0
    gauge_rows = 0
    cell_hist = collections.Counter()
    other, misses, fails = [], [], []
    for r in sel:
        try:
            b = board(r["板帧"])
            a = board(r["落地文件"])
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            fails.append((r["run"], r["t面板"], r["板帧"], r["落地文件"], repr(e)))
            continue
        if b is None or a is None:
            n_fail += 1
            fails.append((r["run"], r["t面板"], r["板帧"], r["落地文件"], "无演目板"))
            continue
        blv, bgg, bfl = b[0], b[1], b[2]
        alv, agg = a[0], a[1]
        dv = {t: alv[t] - blv[t] for t in blv if t in alv and alv[t] != blv[t]}
        cell_hist[len(dv)] += 1
        if len(dv) == 1 and sum(dv.values()) == 1:
            n_1cell += 1
        else:
            n_other += 1
            other.append((r["run"], r["t面板"], dv, r["Lv动格"], r["板帧"], r["落地文件"]))
        # C2：动的那一格 == 板帧 F 指针格
        if len(dv) == 1 and bfl:
            if set(dv) == bfl:
                n_ptr_match += 1
            else:
                n_ptr_miss += 1
                misses.append((r["run"], r["t面板"], sorted(bfl), sorted(dv), r["板帧"]))
        if len(dv) == 1:
            dg = {t: agg[t] - bgg[t] for t in bgg if t in agg and agg[t] != bgg[t]}
            if dg:
                gauge_rows += 1

    print("\n== C1 一次友人外出动几格 act_level ==")
    print(f"  动 1 格(+1)     : {n_1cell}")
    print(f"  动 ≠1 格        : {n_other}   格数直方 {dict(cell_hist)}")
    print(f"  解不出/无板     : {n_fail}")
    for run, t, dv, csv_lv, bf, af in other[:15]:
        print(f"    [{run}] 面板{t} ΔLv={dv} CSV记={csv_lv} {bf}→{af}")
    for run, t, bf, af, why in fails[:10]:
        print(f"    [{run}] 面板{t} {bf}→{af} ⇒ {why}")

    print("\n== C2 落点 == 板帧 F 指针格 ==")
    print(f"  同一格 : {n_ptr_match}    不同格 : {n_ptr_miss}")
    for run, t, fl, dv, bf in misses[:15]:
        print(f"    [{run}] 面板{t} F={fl} 实动={dv} 板帧={bf}")

    print("\n== 旁注 ==")
    print(f"  动 Lv 的那一手同时动了槽的行数 : {gauge_rows} / {n_1cell}")
    print("  （CSV 记的「板_F」= 板帧指针形状，本脚本从报文重算，不读 CSV 该列的判定）")


if __name__ == "__main__":
    main()
