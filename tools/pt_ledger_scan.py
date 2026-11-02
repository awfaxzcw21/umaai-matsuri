#!/usr/bin/env python3
"""真机 PT 逐回合账本 · 一次性扫描器。

产出（都在 E:/HJM/_pttrace/ 下）：
    windows.csv —— 一个「局内请求 → 结算」窗口一行（沿用 _verify_p0/build_actions.py 的窗口口径）
    traj.csv    —— 一个 (run, turn) 一行：该回合内观测到的 skill_point 首/末值

口径要点（与已知陷阱对齐）：
    - 窗口 = 相邻两个局内请求之间的所有 response 报文；pre = 请求前最后一个 chara_info 快照，
      post = 下一个请求前最后一个快照 ⇒ 窗口内差分**不跨回合混事件**（跨回合差分是已知陷阱）。
    - 时间序用 mtime（NNNN 跨连接重置、HHMMSS 跨午夜回绕）。
    - 会话键 = (single_mode_chara_id, start_time)。
"""
from __future__ import annotations

import collections
import csv
import glob
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
OUTDIR = "E:/HJM/_pttrace"
STATS = ("speed", "stamina", "power", "guts", "wiz")
POLLUTE = ("choice_reward_array", "race_add_reward", "race_reward")

CHANNELS = {
    "single_mode_exec_command_request_common": "cmd",
    "single_mode_check_event_request_common": "event",
    "single_mode_race_entry_request_common": "race_entry",
    "single_mode_race_start_request_common": "race_start",
    "single_mode_race_end_request_common": "race_end",
    "single_mode_race_out_request_common": "race_out",
    "single_mode_continue_request_common": "continue",
    "single_mode_start_request_common": "start",
    "single_mode_multi_race_reserve_request_common": "race_reserve",
}
TRANSPARENT = {"event_id"}
TT = {10: "vital", 30: "sp"}


def sort_key(p):
    return (os.path.getmtime(p), os.path.basename(p))


def get_ci(data):
    ci = data.get("chara_info")
    if isinstance(ci, dict) and ci.get("turn") is not None:
        return ci
    lc = data.get("single_mode_load_common")
    if isinstance(lc, dict) and isinstance(lc.get("chara_info"), dict):
        return lc["chara_info"]
    return ci if isinstance(ci, dict) else None


def preds_sp(data):
    """board 预告里 target_type==30（技能Pt）的每格值：command_id -> sp。"""
    out = {}
    for holder, tag in (("home_info", "home"), ("festa_data_set", "festa")):
        arr = ((data.get(holder) or {}).get("command_info_array")) or []
        for c in arr:
            v = 0
            has = False
            for e in c.get("params_inc_dec_info_array") or []:
                if TT.get(e.get("target_type")) == "sp":
                    v += e.get("value") or 0
                    has = True
            if has:
                cid = c.get("command_id")
                out.setdefault(cid, 0)
                out[cid] += v
    return out


def snap(ci, data):
    f = (data.get("festa_data_set") or {}).get("festa_info") or {}
    cr = data.get("command_result") or {}
    return {
        "turn": ci.get("turn"),
        "pt": ci.get("skill_point"),
        "stats": [ci.get(k) for k in STATS],
        "vital": ci.get("vital"),
        "motivation": ci.get("motivation"),
        "fans": ci.get("fans"),
        "n_skills": len(ci.get("skill_array") or []),
        "start_time": ci.get("start_time"),
        "chara": ci.get("single_mode_chara_id"),
        "act_step": f.get("act_training_step"),
        "cmd_result": cr.get("command_id"),
        "pred_sp": preds_sp(data),
    }


def describe(channel, common):
    base = {"ctype": "", "cid": "", "cgroup": "", "select_id": "",
            "turn": common.get("current_turn"), "event_id": "", "event_chara": "",
            "auto_plan": ""}
    if channel == "cmd":
        base.update({
            "ctype": common.get("command_type"),
            "cid": common.get("command_id"),
            "cgroup": common.get("command_group_id"),
            "select_id": common.get("select_id"),
        })
        return base
    if channel == "event":
        base.update({
            "event_id": common.get("event_id"),
            "event_chara": common.get("chara_id"),
            "select_id": common.get("choice_number"),
            "auto_plan": common.get("exec_auto_play_plan_id"),
        })
        return base
    return base


def main():
    files = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=sort_key)
    rows = []
    snaps = []          # 全部 chara_info 快照（含 run 键）
    cur = None
    cur_key = None
    open_act = None
    open_pre = None
    open_pre_key = None
    acc = {k: False for k in POLLUTE}
    n_resp = 0
    stats = collections.Counter()

    def close(post, post_key, flags, nr):
        nonlocal open_act, open_pre, open_pre_key
        if open_act is None:
            return
        same = open_pre_key is not None and open_pre_key == post_key
        rows.append({**open_act,
                     "pre": open_pre, "post": post, "same_run": same, "n_resp": nr,
                     **{"poll_" + k: v for k, v in flags.items()},
                     "clean": same and not any(flags.values())})
        open_act = None
        open_pre = None
        open_pre_key = None

    for path in files:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception as e:  # noqa: BLE001
            stats["decode_fail"] += 1
            continue
        if not isinstance(d, dict):
            continue
        stats["files"] += 1
        if name.endswith("_notify_request.bin"):
            hit = next((k for k in d if k in CHANNELS), None)
            if hit is None:
                if any(k in TRANSPARENT for k in d):
                    stats["transparent"] += 1
                else:
                    for k in d:
                        if isinstance(k, str) and k.endswith("_request_common"):
                            stats["other_req:" + k] += 1
                continue
            channel = CHANNELS[hit]
            common = d[hit] or {}
            stats["req:" + channel] += 1
            if cur is not None:
                close(cur, cur_key, acc, n_resp)
            acc = {k: False for k in POLLUTE}
            n_resp = 0
            open_pre, open_pre_key = cur, cur_key
            open_act = {"channel": channel, "file": name,
                        "req_mtime": os.path.getmtime(path),
                        **describe(channel, common)}
            continue
        if not name.endswith("_notify_response.bin"):
            continue
        data = d.get("data") or {}
        for k in POLLUTE:
            if data.get(k):
                acc[k] = True
        n_resp += 1
        ci = get_ci(data)
        if ci is None:
            stats["resp_no_ci"] += 1
            continue
        s = snap(ci, data)
        k2 = (s["chara"], s["start_time"])
        snaps.append({"file": name, "mtime": os.path.getmtime(path), "run_key": k2, **s})
        cur, cur_key = s, k2
    if cur is not None:
        close(cur, cur_key, acc, n_resp)

    # ---- windows.csv ----
    with open(os.path.join(OUTDIR, "windows.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        head = ["run", "channel", "turn", "ctype", "cid", "cgroup", "select_id",
                "event_id", "event_chara", "auto_plan", "pre_turn", "pre_pt", "post_turn",
                "post_pt", "d_pt", "d_skills", "n_resp", "clean", "same_run",
                "poll_choice_reward_array", "poll_race_add_reward", "poll_race_reward",
                "pred_sp_cid", "act_step_pre", "act_step_post", "file", "req_mtime"]
        w.writerow(head)
        for r in rows:
            pre, post = r["pre"], r["post"]
            run = ("%s@%s" % (post["chara"], post["start_time"])) if post else None
            d_pt = d_skills = None
            if pre and post and pre["pt"] is not None and post["pt"] is not None:
                d_pt = post["pt"] - pre["pt"]
                d_skills = post["n_skills"] - pre["n_skills"]
            cid = r["cid"]
            pred = ""
            if isinstance(cid, int) and isinstance(pre, dict):
                pred = pre["pred_sp"].get(cid, "")
            w.writerow([run, r["channel"], r["turn"], r["ctype"], cid, r["cgroup"],
                        r["select_id"], r["event_id"], r["event_chara"], r["auto_plan"],
                        pre["turn"] if pre else "", pre["pt"] if pre else "",
                        post["turn"] if post else "", post["pt"] if post else "",
                        d_pt, d_skills, r["n_resp"], int(r["clean"]), int(r["same_run"]),
                        int(r["poll_choice_reward_array"]), int(r["poll_race_add_reward"]),
                        int(r["poll_race_reward"]),
                        pred,
                        pre["act_step"] if pre else "", post["act_step"] if post else "",
                        r["file"], r["req_mtime"]])

    # ---- traj.csv：(run, turn) -> 该回合观测到的 pt 首/末、五维末值 ----
    per = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {"pt_first": None, "pt_last": None, "snap_n": 0, "files": [],
                 "stats_last": None, "n_skills": None, "fans": None, "act_steps": set()}))
    for s in snaps:
        if s["run_key"][1] is None or s["turn"] is None:
            continue
        slot = per[s["run_key"]][s["turn"]]
        if slot["pt_first"] is None:
            slot["pt_first"] = s["pt"]
        slot["pt_last"] = s["pt"]
        slot["snap_n"] += 1
        slot["stats_last"] = s["stats"]
        slot["n_skills"] = s["n_skills"]
        slot["fans"] = s["fans"]
        if s["act_step"] is not None:
            slot["act_steps"].add(s["act_step"])
        if len(slot["files"]) < 3:
            slot["files"].append(s["file"])
    with open(os.path.join(OUTDIR, "traj.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["chara", "start_time", "run", "turn", "pt_first", "pt_last",
                    "d_pt_from_prev_turn", "snap_n", "n_skills", "fans", "act_steps", "files"])
        n_runs = 0
        for rk in sorted(per, key=lambda x: (str(x[0]), str(x[1]))):
            n_runs += 1
            prev = None
            for t in sorted(per[rk]):
                slot = per[rk][t]
                w.writerow([rk[0], rk[1], "%s@%s" % rk, t, slot["pt_first"], slot["pt_last"],
                            (slot["pt_last"] - prev) if (prev is not None and slot["pt_last"] is not None) else "",
                            slot["snap_n"], slot["n_skills"], slot["fans"],
                            "|".join(str(x) for x in sorted(slot["act_steps"])),
                            ";".join(slot["files"])])
                if slot["pt_last"] is not None:
                    prev = slot["pt_last"]
    json.dump({str(k): v for k, v in stats.items()},
              open(os.path.join(OUTDIR, "scan_stats.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("files=%d windows=%d snapshots=%d runs=%d" %
          (stats["files"], len(rows), len(snaps), n_runs))
    print(dict(stats))


if __name__ == "__main__":
    main()
