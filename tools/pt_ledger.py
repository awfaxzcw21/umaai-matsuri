#!/usr/bin/env python3
"""真机 PT 逐回合账本 v2（per-run / per-turn / per-source）。

输入：E:/HJM/_pttrace/windows.csv + traj.csv（scan_ledger.py 产出）
输出：ledger_turn.csv / ledger_run.csv / ledger_summary.csv + 控制台报告

窗口口径（沿用 _verify_p0/build_actions.py 已定案的做法）：一个窗口 = 相邻两个**局内请求**
之间的全部结算报文；pre = 请求前最后一个 chara_info 快照，post = 下一请求前最后一个。
⇒ 差分**不跨请求**，因此不会把事件增益混进训练差分（跨回合差分是已知陷阱）。
只统计 same_run=1 的窗口（pre、post 同一局），否则 run 边界会把上一局的 PT 当成扣除。

归因规则（一手=报文可读出的通道/回合；比率类判定在 R5 说明）：
  R1 训练手     channel=cmd ctype=1 cid∈{101..106}
  R2 稽古/演目  channel=cmd ctype=1 cid∈{5101..5105}
  R3 外出       channel=cmd ctype=3 cgroup∈{301,304,390,1106,3606}（master.mdb single_mode_outing 5 行）
  R4 发表会     channel=event event_id∈{215010,215011,215012,215013,215014,215015,215027,215028}
                且落点回合∈{24,48,68,78}（前三场=master.mdb single_mode_15_schedule；第 4 场只有报文）
  R5 比赛       channel=event 且该 event_id 在全 corpus 里**所有** d_pt>0 窗口都落在「同回合有
                race_* 请求」的回合上（rate=100%，n>0）；混合速率的 id 不算比赛（归 R6）
  R6 事件       其余 event 窗口
  R7 支出       任何 d_pt<0 的窗口（终点=技能购入 event_id 6 / 演目购入 21268）
  R8 其他       cmd ctype∈{7,8}、race_*、continue、start、reserve、factor_select、finish
  未归因        轨迹回合差分 − 该回合窗口差分合计（按 post 落点回合 join）
"""
from __future__ import annotations

import csv
import collections
import os

D = "E:/HJM/_pttrace"
CP_IDS = {215010, 215011, 215012, 215013, 215014, 215015, 215027, 215028}
CP_TURNS = {24, 48, 68, 78}
OUTING_GROUPS = {301, 304, 390, 1106, 3606}
TRAIN_IDS = {101, 102, 103, 104, 105, 106}
KEIKO_IDS = {5101, 5102, 5103, 5104, 5105}


def num(x):
    try:
        return int(float(x))
    except Exception:
        return None


def load():
    rows = list(csv.DictReader(open(os.path.join(D, "windows.csv"), encoding="utf-8")))
    for r in rows:
        r["d"] = num(r["d_pt"])
        r["Treq"] = num(r["turn"])
        r["Tpost"] = num(r["post_turn"])
        r["T"] = r["Tpost"] if r["Tpost"] is not None else r["Treq"]
    race_turns = collections.defaultdict(set)
    for r in rows:
        if r["channel"].startswith("race") and r["run"]:
            for t in (r["Treq"], r["Tpost"]):
                if t is not None:
                    race_turns[r["run"]].add(t)
    return rows, race_turns


def race_ids_by_rate(rows, race_turns):
    """每个 event_id 的正 PT 窗口里落在比赛回合的比例；rate=1.0 判为「赛后奖励通道」。"""
    pos = collections.Counter()
    onr = collections.Counter()
    for r in rows:
        if r["channel"] != "event" or not r["d"] or r["d"] < 0:
            continue
        eid = num(r["event_id"])
        pos[eid] += 1
        tset = race_turns.get(r["run"], set())
        if any(t in tset for t in (r["Treq"], r["Tpost"]) if t is not None):
            onr[eid] += 1
    return {eid for eid, n in pos.items() if n and onr[eid] == n}, pos, onr


def main():
    rows, race_turns = load()
    RACE_IDS, pos_all, onr_all = race_ids_by_rate(rows, race_turns)
    for r in rows:
        # 未归因 join 键 = 变化被观测到的那一回合 = post 快照回合。
        # 三种键实测（残差回合数 / |残差|）：post 15/11,634；pre 213/20,699；req 213/20,699
        # ⇒ 取 post（见 ledger.py 顶注与 E:/HJM/_pttrace/join_test.py 复现）。
        r["Tjoin"] = r["T"]

    def classify(r):
        if r["d"] is not None and r["d"] < 0:
            return "支出(技能购入等)"
        ch = r["channel"]
        if ch == "cmd":
            ctype, cid, cg = num(r["ctype"]), num(r["cid"]), num(r["cgroup"])
            if ctype == 1 and cid in TRAIN_IDS:
                return "训练手"
            if ctype == 1 and cid in KEIKO_IDS:
                return "稽古/演目"
            if ctype == 3 and cg in OUTING_GROUPS:
                return "外出"
            return "其他命令"
        if ch == "event":
            eid = num(r["event_id"])
            if eid in CP_IDS and (r["T"] in CP_TURNS or r["Treq"] in CP_TURNS):
                return "发表会"
            if eid in RACE_IDS:
                return "比赛"
            return "事件"
        if ch == "start":
            return "其他命令"
        return "其他通道"

    traj = list(csv.DictReader(open(os.path.join(D, "traj.csv"), encoding="utf-8")))
    for t in traj:
        t["run"] = t["chara"] + "@" + t["start_time"]
        t["turn"] = num(t["turn"])
        t["pt_i"] = num(t["pt_last"])
    traj_by_run = collections.defaultdict(list)
    for t in traj:
        traj_by_run[t["run"]].append(t)
    full_runs = sorted(k for k, v in traj_by_run.items()
                       if len(v) >= 60 and all(x["pt_i"] is not None for x in v))

    per = collections.defaultdict(lambda: [0, 0])       # (run,src)->[n,PT]（仅整局 run）
    per_all = collections.defaultdict(lambda: [0, 0])   # 全 corpus（含半局）
    covered = collections.defaultdict(lambda: collections.defaultdict(int))
    n_skipped_crossrun = sum(1 for r in rows if r["run"] and r["same_run"] == "0")
    for r in rows:
        if not r["run"] or r["same_run"] != "1" or r["d"] is None or r["T"] is None:
            continue
        src = classify(r)
        per_all[(r["run"], src)][0] += 1
        per_all[(r["run"], src)][1] += r["d"]
        if r["run"] in full_runs:
            per[(r["run"], src)][0] += 1
            per[(r["run"], src)][1] += r["d"]
            covered[r["run"]][r["Tjoin"]] += r["d"]

    sources = sorted({s for (_, s) in per_all} | {"开局初始PT"})
    n_turns = sum(len(traj_by_run[r]) for r in full_runs)
    gross = sum(v[1] for k, v in per.items() if v[1] > 0)
    spend = sum(v[1] for k, v in per.items() if v[1] < 0)

    # 未归因（按「变化被观测到的回合」= pre 快照回合 join）
    resid = collections.Counter()
    resid_abs = collections.Counter()
    resid_turns = collections.Counter()
    resid_rows = []
    for run in full_runs:
        prev = None
        for t in traj_by_run[run]:
            if prev is not None:
                d = t["pt_i"] - prev
                w = covered[run].get(t["turn"], 0)
                if d - w:
                    resid[run] += d - w
                    resid_abs[run] += abs(d - w)
                    resid_turns[run] += 1
                    resid_rows.append({"run": run, "turn": t["turn"], "traj_d": d,
                                       "window_d": w, "residual": d - w,
                                       "snap_n": t["snap_n"], "act_steps": t["act_steps"],
                                       "files": t["files"]})
            prev = t["pt_i"]

    with open(os.path.join(D, "ledger_turn.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["run", "turn", "traj_d", "window_d", "residual",
                                          "snap_n", "act_steps", "files"])
        w.writeheader()
        w.writerows(resid_rows)

    # ---- 开局初始 PT（报文 t_min 的第一个 chara_info 快照）：不是窗口差分，单列 ----
    init_of = {}
    for run in full_runs:
        v = num(traj_by_run[run][0]["pt_first"])
        if v is None:
            v = traj_by_run[run][0]["pt_i"]
        init_of[run] = v
    init_total = sum(init_of.values())

    print("整局 run=%d，(run,turn) 观测点=%d，跨局窗口剔除=%d" %
          (len(full_runs), n_turns, n_skipped_crossrun))
    print("毛增合计(窗口正差分)=%d，支出合计=%d，开局初始PT合计=%d" % (gross, spend, init_total))
    print("\n=== 逐源账本（%d 局整局；PT/回合 = 该源 PT ÷ %d 个 (run,turn) 观测点）===" %
          (len(full_runs), n_turns))
    print("%-18s %6s %9s %9s %8s %6s %9s" %
          ("source", "n手", "PT", "PT/回合", "占毛增", "n局", "mean/手"))
    summary = []
    for s in sources:
        if s == "开局初始PT":
            sm, nr, runs_present = init_total, 0, len(full_runs)
        else:
            sm = sum(per[k][1] for k in per if k[1] == s)
            nr = sum(per_all[k][0] for k in per_all if k[1] == s and k[0] in full_runs)
            runs_present = len({k[0] for k in per if k[1] == s})
        share = sm / gross * 100 if gross else 0
        summary.append((s, nr, sm, sm / n_turns, share, runs_present,
                        (sm / nr) if nr else 0.0))
    for L in sorted(summary, key=lambda x: -x[2]):
        if L[0] == "开局初始PT":
            print("%-18s %6s %9d %9.2f %7.1f%% %6d %9s" %
                  (L[0], "-", L[2], L[3], L[4], L[5], "-"))
        else:
            print("%-18s %6d %9d %9.2f %7.1f%% %6d %9.1f" % L)
    tot_resid = sum(resid.values())
    tot_abs = sum(resid_abs.values())
    print("%-18s %6s %9d %9.2f %7.1f%% %6d %9s" %
          ("未归因(净残差)", "-", tot_resid, tot_resid / n_turns,
           tot_resid / gross * 100 if gross else 0,
           len({r for r in full_runs if resid[r]}), "-"))
    print("  净残差落在 %d 个 (run,turn)（共 %d 回合，%.1f%%）；|残差| 合计=%d（占毛增 %.2f%%）" %
          (sum(resid_turns.values()), n_turns, 100.0 * sum(resid_turns.values()) / n_turns,
           tot_abs, tot_abs / gross * 100 if gross else 0))

    with open(os.path.join(D, "ledger_summary.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["source", "n_windows_fullruns", "pt_fullruns", "pt_per_turn",
                    "share_of_gross_pct", "pt_per_run", "n_runs_present", "pt_per_window",
                    "n_windows_all_corpus", "pt_all_corpus"])
        nf = len(full_runs) or 1
        for s in sources:
            if s == "开局初始PT":
                nr, sm, n_all, p_all = 0, init_total, 0, init_total
            else:
                nr = sum(per_all[k][0] for k in per_all if k[1] == s and k[0] in full_runs)
                sm = sum(per[k][1] for k in per if k[1] == s)
                n_all = sum(per_all[k][0] for k in per_all if k[1] == s)
                p_all = sum(per_all[k][1] for k in per_all if k[1] == s)
            w.writerow([s, nr, sm, round(sm / n_turns, 3),
                        round(sm / gross * 100, 2) if gross else "", round(sm / nf, 1),
                        len({k[0] for k in per if k[1] == s}),
                        round(sm / nr, 1) if nr else "", n_all, p_all])
        w.writerow(["未归因(净残差)", "", tot_resid, round(tot_resid / n_turns, 3),
                    round(tot_resid / gross * 100, 2) if gross else "",
                    round(tot_resid / nf, 1),
                    len({r for r in full_runs if resid[r]}), "", "", ""])
        w.writerow(["|未归因| 绝对值", "", tot_abs, round(tot_abs / n_turns, 3),
                    round(tot_abs / gross * 100, 2) if gross else "",
                    round(tot_abs / nf, 1), len({r for r in full_runs if resid_abs[r]}),
                    "", "", ""])

    with open(os.path.join(D, "ledger_run.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run"] + sources + ["未归因", "|未归因|", "闭合差", "回合数",
                                        "末快照PT", "峰值PT"])
        for run in full_runs:
            vals = [per.get((run, s), [0, 0])[1] if s != "开局初始PT" else init_of[run]
                    for s in sources]
            peak = max(x["pt_i"] for x in traj_by_run[run])
            final = traj_by_run[run][-1]["pt_i"]
            expect = init_of[run] + sum(v[1] for k, v in per.items()
                                         if k[0] == run and k[1] != "开局初始PT")
            w.writerow([run] + vals + [resid[run], resid_abs[run], final - expect,
                                       len(traj_by_run[run]), final, peak])

    print("\n=== 逐局（源×run）===")
    hdr = ["run", "回合", "末PT", "峰PT"] + [s for s in sources if any(per.get((r, s), [0, 0])[1] for r in full_runs)] + ["未归因"]
    print(" | ".join(hdr))
    for run in full_runs:
        cells = [run[:12], str(len(traj_by_run[run])),
                 str(traj_by_run[run][-1]["pt_i"]),
                 str(max(x["pt_i"] for x in traj_by_run[run]))]
        for s in sources:
            if any(per.get((r, s), [0, 0])[1] for r in full_runs):
                cells.append(str(per.get((run, s), [0, 0])[1]))
        cells.append(str(resid[run]))
        print(" | ".join(cells))

    print("\n=== R5 判据明细：正 PT 窗口的 event_id race-rate ===")
    rate = sorted(((eid, pos_all[eid], onr_all[eid]) for eid in pos_all),
                  key=lambda x: -(x[1]))[:18]
    for eid, n, nr in rate:
        print("  eid=%-8s 正PT窗口=%-4d 比赛回合上=%-4d rate=%.2f %s" %
              (eid, n, nr, nr / n, "→比赛" if nr == n else ""))
    print("\nwrote ledger_turn.csv / ledger_run.csv / ledger_summary.csv")


if __name__ == "__main__":
    main()
