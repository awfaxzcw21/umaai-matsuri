#!/usr/bin/env python3
"""真机 PT 账本的**分段**版：把同一套归因规则（R1–R8）按回合切一刀。

为什么要有它：门禁从报文锚点 t30 起跑，引擎侧的逐源台账只覆盖 t30→终局；
拿真机**整局**账本去比会把 t1–t30 的增益算进缺口，比出假的「谁少了一截」。
本脚本对同一局同时输出「整局」与「turn > --from」两行，整局那行必须与
`tools/pt_ledger.py` 打印的逐局数字逐位对上 —— 那是这份分段的可信度检查。

从仓根跑：`python tools/pt_ledger_segment.py --run 179 --from 30`
输入同 pt_ledger：`E:/HJM/_pttrace/windows.csv`（一手报文的窗口差分，由 scan_ledger.py 产出）。
"""
from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pt_ledger as L  # noqa: E402  复用同一套常量与 load/race_ids_by_rate


def classify(r, race_ids):
    """与 pt_ledger.main() 内层 classify 逐条同规则（改动必须两边同步）。"""
    if r["d"] is not None and r["d"] < 0:
        return "支出(技能购入等)"
    ch = r["channel"]
    if ch == "cmd":
        ctype, cid, cg = L.num(r["ctype"]), L.num(r["cid"]), L.num(r["cgroup"])
        if ctype == 1 and cid in L.TRAIN_IDS:
            return "训练手"
        if ctype == 1 and cid in L.KEIKO_IDS:
            return "稽古/演目"
        if ctype == 3 and cg in L.OUTING_GROUPS:
            return "外出"
        return "其他命令"
    if ch == "event":
        eid = L.num(r["event_id"])
        if eid in L.CP_IDS and (r["T"] in L.CP_TURNS or r["Treq"] in L.CP_TURNS):
            return "发表会"
        if eid in race_ids:
            return "比赛"
        return "事件"
    if ch == "start":
        return "其他命令"
    return "其他通道"


def tally(rows, run_prefix, turn_from, race_ids):
    per = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        if not (r["run"] or "").startswith(run_prefix):
            continue
        if r["same_run"] != "1":
            continue
        if r["d"] is None or r["d"] <= 0:
            continue
        if (r["T"] or 0) <= turn_from:
            continue
        src = classify(r, race_ids)
        per[src][0] += 1
        per[src][1] += r["d"]
    return per


def show(title, per):
    print(f"\n=== {title} ===")
    print("source            n手      PT   mean/手")
    for src, (n, pt) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"{src:<16} {n:>5}  {pt:>7}  {pt / n if n else 0:>7.1f}")
    print(f"{'合计(正差分)':<16} {sum(v[0] for v in per.values()):>5}  "
          f"{sum(v[1] for v in per.values()):>7}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="179", help="run 前缀，如 179 匹配 179@2026-10-…")
    ap.add_argument("--from", type=int, default=30, dest="turn_from",
                    help="只统计 post 落点回合 > 该值的窗口（门禁锚点 = 30）")
    a = ap.parse_args()

    rows, race_turns = L.load()
    race_ids, _, _ = L.race_ids_by_rate(rows, race_turns)
    prefix = f"{a.run}@"
    show(f"整局（应与 tools/pt_ledger.py 的逐局行一致）run={prefix}",
         tally(rows, prefix, 0, race_ids))
    show(f"分段 run={prefix} 回合>{a.turn_from}（与引擎 t30 起跑的台账同口径）",
         tally(rows, prefix, a.turn_from, race_ids))


if __name__ == "__main__":
    main()
