#!/usr/bin/env python3
"""自选/目标外比赛逐场扫描 —— #25 的一手测量。

一手源 = `E:/HJM/jidianbeiAI0.22/out/hachimi_raw` 的 msgpack 报文（只读）
+ `E:/HJM/_ref01/matsuri-ref-01/data/master.mdb`（只读，`race` / `single_mode_program`）。

口径：

- 一场比赛 = 一条 `single_mode_race_entry_request_common`（字段 `program_id`、
  `current_turn`）；它后面那个快照的 `chara_info` 差分 = 该场（含赛后事件）的实到。
- **是否在预告赛程内** = 该 `program_id` 是否出现在本局任一带
  `race_condition_array`（下场比赛列表）的报文里。出现 = 预告内；没出现 = 赛程外
  （玩家自选/预约打的那场）。
- 等级链 `single_mode_program.race_instance_id → race_instance.race_id → race.grade`
  （1=G1 2=G2 3=G3，其余按表值原样报出，不翻译）；`need_fan_count` 一并报出，
  用来判「引擎没有粉丝系统」这道门槛到底挡不挡得住这些场。

跑法：

    python tools/free_race_scan.py            # 全部 run 的逐场表 + 汇总
    python tools/free_race_scan.py --run 179  # 只看某个 run
    python tools/free_race_scan.py --csv out/_free_race_scan.csv

不改任何配置、不算任何拟合系数。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import os
import sqlite3
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

PK = ("speed", "stamina", "power", "guts", "wiz")
MK = tuple("max_" + k for k in PK)


def program_grades(mdb):
    """program_id -> (grade, group, need_fan_count, month, half)。只读打开，取不到就返回空表。"""
    try:
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % mdb.replace("\\", "/"), uri=True)
    except Exception as e:  # noqa: BLE001
        print("!! master.mdb 打不开（%s）⇒ 等级/粉丝门槛列留空" % e)
        return {}
    q = (
        "select p.id, r.grade, r.\"group\", p.need_fan_count, p.month, p.half "
        "from single_mode_program p "
        "join race_instance i on i.id = p.race_instance_id "
        "join race r on r.id = i.race_id"
    )
    out = {}
    try:
        for pid, grade, grp, fan, month, half in con.execute(q):
            out[int(pid)] = (grade, grp, fan, month, half)
    except Exception as e:  # noqa: BLE001
        print("!! 等级链查询失败（%s）" % e)
    finally:
        con.close()
    return out


def run_key(ci):
    return (ci.get("single_mode_chara_id"), ci.get("single_mode_start_time"))


def scan(raw_dir):
    """返回 (逐场记录, 每局带板赛程里出现过的 program_id 集合)。"""
    files = sorted(glob.glob(os.path.join(raw_dir, "*.bin")), key=PS.sort_key)
    scheduled = collections.defaultdict(set)  # run -> {program_id}
    events = []                               # (run, turn, program_id, seq)
    seq = 0
    snap = None       # 最近一个 chara_info 快照
    snap_seq = -1
    for path in files:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_notify_request.bin"):
            req = d.get("single_mode_race_entry_request_common")
            if isinstance(req, dict) and req.get("program_id") is not None and snap is not None:
                events.append(
                    {
                        "seq": seq,
                        "run": snap_key,
                        "turn_req": req.get("current_turn"),
                        "turn_snap": snap["turn"],
                        "program_id": req.get("program_id"),
                        "reserve": snap.get("reserve_race_program_id"),
                    }
                )
                seq += 1
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or not all(k in ci for k in MK):
            continue
        key = run_key(ci)
        for e in data.get("race_condition_array") or []:
            if isinstance(e, dict) and e.get("program_id") is not None:
                scheduled[key].add(e["program_id"])
        snap = {
            "turn": ci.get("turn"),
            "five": [ci.get(k) for k in PK],
            "sp": ci.get("skill_point"),
            "vital": ci.get("vital"),
            "fans": ci.get("fans"),
            "race_program_id": ci.get("race_program_id"),
            "reserve_race_program_id": ci.get("reserve_race_program_id"),
        }
        snap_key = key
        snap_seq = seq
    return events, scheduled, snap_seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=PS.RAW)
    ap.add_argument("--mdb", default="E:/HJM/_ref01/matsuri-ref-01/data/master.mdb")
    ap.add_argument("--run", default="", help="只报这个 run 前缀（如 179）")
    ap.add_argument("--csv", default="")
    args = ap.parse_args()

    grades = program_grades(args.mdb)
    events, scheduled, _ = scan(args.raw)
    rows = []
    prev_by_run = {}
    for ev in events:
        # 该场之前的最后一个快照由 scan 顺序保证；这里用「同一局上一条记录」近似差分基线
        key = ev["run"]
        rows.append(ev)
        prev_by_run.setdefault(key, 0)
        prev_by_run[key] += 1

    # 差分：对每个 run 把 race_entry 与其后的第一个快照对齐（重走一遍，保留快照序列）
    snaps = collections.defaultdict(list)
    files = sorted(glob.glob(os.path.join(args.raw, "*.bin")), key=PS.sort_key)
    cur = None
    for path in files:
        name = os.path.basename(path)
        if name.endswith("_notify_request.bin"):
            continue
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or not all(k in ci for k in MK):
            continue
        snaps[run_key(ci)].append((ci.get("turn"), ci.get("fans"), ci.get("skill_point"), [ci.get(k) for k in PK]))

    out = []
    for ev in events:
        seq = snaps.get(ev["run"]) or []
        pos = None
        for idx, (t, _f, _s, _fv) in enumerate(seq):
            if t == ev["turn_snap"]:
                pos = idx
                break
        d_pt = d_vital = None
        d_five = [None] * 5
        fans = None
        if pos is not None and pos + 1 < len(seq):
            t2, f2, s2, fv2 = seq[pos + 1]
            t1, f1, s1, fv1 = seq[pos]
            d_pt = (s2 or 0) - (s1 or 0)
            d_five = [(fv2[i] or 0) - (fv1[i] or 0) for i in range(5)]
            fans = f1
        pid = ev["program_id"]
        gr = grades.get(pid, (None, None, None, None, None))
        out.append(
            {
                "run": ev["run"][0],
                "start": ev["run"][1],
                "turn_snap": ev["turn_snap"],
                "turn_req": ev["turn_req"],
                "program_id": pid,
                "grade": gr[0],
                "group": gr[1],
                "need_fan": gr[2],
                "month_half": "%s/%s" % (gr[3], gr[4]),
                "in_schedule": pid in scheduled.get(ev["run"], set()),
                "d_pt": d_pt,
                "d_five": "|".join(str(x) for x in d_five),
                "fans_at_entry": fans,
            }
        )

    if args.run:
        out = [r for r in out if str(r["run"]).startswith(str(args.run))]

    print("== 逐场 race_entry（一手报文）==")
    print("run  turn  program  group  grade  need_fan  预告内  ΔPt  Δ五维")
    for r in out:
        print(
            "%-4s %-5s %-8s %-6s %-6s %-9s %-6s %-5s %s"
            % (
                r["run"],
                r["turn_snap"],
                r["program_id"],
                r["group"],
                r["grade"],
                r["need_fan"],
                "是" if r["in_schedule"] else "**外**",
                r["d_pt"],
                r["d_five"],
            )
        )

    ins = [r for r in out if r["in_schedule"]]
    outs = [r for r in out if not r["in_schedule"]]
    print("\n== 汇总 ==")
    print("场次 %d（预告内 %d / 赛程外 %d）" % (len(out), len(ins), len(outs)))
    for tag, grp in (("预告内", ins), ("赛程外", outs)):
        if not grp:
            continue
        by_grade = collections.Counter(r["grade"] for r in grp)
        pts = [r["d_pt"] for r in grp if r["d_pt"] is not None]
        print(
            "  %s：等级分布 %s｜ΔPt n=%d Σ=%s 中位=%s 范围=%s"
            % (
                tag,
                dict(by_grade),
                len(pts),
                sum(pts) if pts else 0,
                sorted(pts)[len(pts) // 2] if pts else "-",
                "%s..%s" % (min(pts), max(pts)) if pts else "-",
            )
        )
    turns = sorted({r["turn_snap"] for r in outs})
    print("  赛程外发生的回合：%s" % turns)
    # 粉丝门槛判据：报名那一刻的 fans 够不够 need_fan_count（#25 的关键，全样本统计）
    known = [r for r in out if r["need_fan"] and r["fans_at_entry"] is not None]
    under = [r for r in known if r["fans_at_entry"] < r["need_fan"]]
    print("  带门槛且有粉丝读数的场次：%d｜其中「报名时粉丝不足仍去打」%d 场" % (len(known), len(under)))
    for r in under[:12]:
        print("     run%s turn%s program%s 需 %s 粉，实际 %s" %
              (r["run"], r["turn_snap"], r["program_id"], r["need_fan"], r["fans_at_entry"]))
    nofan = [r for r in out if r["fans_at_entry"] is None]
    if nofan:
        print("  （%d 场没配到带粉丝读数的快照，不参与上面判定）" % len(nofan))
    wint = [r for r in out if r["turn_snap"] in (44, 45, 68, 69)]
    print("  自选窗口回合（44/45/68/69）场次 %d，need_fan 取值 %s"
          % (len(wint), sorted({r["need_fan"] for r in wint if r["need_fan"]})))

    # race.group 分层：门槛和「哪几场是本剧本自己的赛」都由它决定，别把两级混在一张表里看
    print("\n== 按 race.group × grade 分档 ==")
    cell = collections.defaultdict(list)
    for r in out:
        cell[(r["group"], r["grade"])].append(r)
    for (grp, grade) in sorted(cell, key=lambda k: (str(k[0]), str(k[1]))):
        rs = cell[(grp, grade)]
        gates = sorted({r["need_fan"] for r in rs if r["need_fan"]})
        print("  group=%-4s grade=%-5s 场次=%-4s 门槛=%s%s 回合=%s"
              % (grp, grade, len(rs), gates,
                 "（含 %d 场零门槛）" % sum(1 for r in rs if not r["need_fan"])
                 if any(not r["need_fan"] for r in rs) else "",
                 sorted({r["turn_snap"] for r in rs})))

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
            w.writeheader()
            w.writerows(out)
        print("\nwrote", args.csv, len(out), "rows")


if __name__ == "__main__":
    main()
