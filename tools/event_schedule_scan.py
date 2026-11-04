#!/usr/bin/env python3
"""#57 剧本自持事件表：用一手报文定骏大祭（scen 15）的事件日程、触发率与身份。

    python tools/event_schedule_scan.py              # 全量扫（约 5 分钟）+ 出报告
    python tools/event_schedule_scan.py --from-cache # 复用 out/ 缓存只重出报告

三条一手来源，各自标注，不混用：
  报文   `E:/HJM/jidianbeiAI0.22/out/hachimi_raw`（`data.unchecked_event_array` = 待播队列；
         `single_mode_check_event_request_common.event_id` = 客户端明说本跳在结算谁）
  master `E:/HJM/_ref01/matsuri-ref-01/data/master.mdb` 的 `single_mode_story_data`（只读，只做身份核对）
  引擎   `data/constants.json` 的 `no_event_turns`（**引擎 0 基轴**，只是拿来对表，不改它）

两条**互相独立**的结算判据（对账写在 §1）：
  J2 上一条 check_event 请求带的 `event_id`；
  J1 相邻 `chara_info` 报文里「前一跳队列有、本跳没有」那条（`docs/继承剂量_种马输入_20261104.md` §10a）。

三条口径纪律（第一遍踩过，写死在这里）：
  · 身份键 = `(event_id, story_id)`，不是 `event_id`：同一个 `event_id` 会挂不同 `story_id`
    （10002 同时挂 830289001 / 830311001），只按 id 聚合会把两个事件并成一个。
  · `event_id` **不是稳定外键**：只有它本身就是 `single_mode_story_data.id`（5–6 位行号，如 215022）
    才连得上；低位 id（7040/7041/1011/3001/7005…）是另一个命名空间，按 id 查会连到别人的故事。
    其余一律用 `story_id` join（脚本 §7 实测出各族的命中率）。
  · 效果只在「同一面板回合的相邻跳」且**本跳响应不带赛事/选项奖励**（`PS.POLLUTE`）时取，
    否则 Δ 里混的是比赛结算不是事件结算。

用途边界：只出日程、触发率与身份，不折算收益常数；`data/` 一个字不改，接线要另做裁决。
回合轴：报文 = 面板 = `master.mdb single_mode_turn.turn`（1 基）；引擎 = 报文 − 1。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import os
import sqlite3
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

RAW = PS.RAW
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
CACHE = "out/_event_sched_cache_20261105.json"
OUT_CSV = "out/_event_sched_events_20261105.csv"
OUT_TURN = "out/_event_sched_turns_20261105.csv"
OUT_TXT = "out/_event_sched_report_20261105.txt"
AXES = PS.STATS
SCEN = 15
EVENT_CH = "single_mode_check_event_request_common"
MATSURI_STORY = "400015"          # 剧本 15 自持事件的 story_id 前缀


def family(story_id):
    s = str(story_id or "")
    if s.startswith(MATSURI_STORY):
        return "剧本15自持"
    if s.startswith("4000"):
        return "剧本级(其他)"
    if s.startswith("501"):
        return "角色事件"
    if s.startswith(("80", "82", "83")):
        return "卡/支援事件"
    return "其他"


def ev_brief(e):
    """一条队列事件的最小可读摘录（键名实测，见报告 §2 字段直方图）。"""
    ec = e.get("event_contents_info") or {}
    ch = ec.get("choice_array") or []
    return {
        "event_id": e.get("event_id"),
        "story_id": e.get("story_id"),
        "chara_id": e.get("chara_id"),
        "play_timing": e.get("play_timing"),
        "n_choice": len(ch),
        "succ": e.get("succession_event_info") is not None,
        "mg": e.get("minigame_result") is not None,
        "sup_card": ec.get("support_card_id"),
        "tips_partner": ec.get("tips_training_partner_id"),
    }


def scan():
    paths = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=PS.sort_key)
    runs = {}
    stats = collections.Counter()
    req_pending = {"ch": None, "event_id": None}
    field_hist = collections.Counter()
    play_timing = collections.Counter()

    for path in paths:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            stats["decode_fail"] += 1
            continue
        if not isinstance(d, dict):
            continue
        stats["files"] += 1
        if name.endswith("_notify_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            if hit == EVENT_CH:
                c = d[hit] or {}
                req_pending = {"ch": "event", "file": name, "event_id": c.get("event_id"),
                               "choice_number": c.get("choice_number"),
                               "auto_plan": c.get("exec_auto_play_plan_id"),
                               "req_turn": c.get("current_turn")}
                stats["req_event"] += 1
            else:
                req_pending = {"ch": PS.CHANNELS.get(hit, "other"), "file": name,
                               "event_id": None}
            continue

        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        viewer = d.get("viewer_id")
        if viewer is None and isinstance(d.get("data_headers"), dict):
            viewer = d["data_headers"].get("viewer_id")
        run = "%s@%s@%s" % (viewer, ci.get("single_mode_chara_id"), ci.get("start_time"))
        r = runs.setdefault(run, {"run": run, "viewer": viewer,
                                  "chara": ci.get("single_mode_chara_id"),
                                  "card_id": ci.get("card_id"),
                                  "start_time": ci.get("start_time"),
                                  "scen": ci.get("scenario_id"), "pkts": []})
        uq = data.get("unchecked_event_array")
        q = []
        if isinstance(uq, list):
            for e in uq:
                if not isinstance(e, dict):
                    continue
                b = ev_brief(e)
                q.append(b)
                play_timing[b["play_timing"]] += 1
                for k in e:
                    field_hist[str(k)] += 1
        poll = sum(1 for k in PS.POLLUTE if data.get(k))
        stats["polluted_pkts"] += 1 if poll else 0
        r["pkts"].append(dict(
            turn=ci.get("turn"),
            five=[int(ci.get(a) or 0) for a in AXES],
            max=[int(ci.get("max_" + a) or 0) for a in AXES],
            dmax=[int(ci.get("default_max_" + a) or 0) for a in AXES],
            vital=int(ci.get("vital") or 0), mot=int(ci.get("motivation") or 0),
            fans=int(ci.get("fans") or 0), sp=int(ci.get("skill_point") or 0),
            nsk=len(ci.get("skill_array") or []),
            file=name, q=q, req=req_pending, poll=poll))
        r["last_turn"] = ci.get("turn")
    return {"stats": dict(stats), "runs": runs, "field_hist": dict(field_hist),
            "play_timing": {str(k): v for k, v in play_timing.items()},
            "meta": {"raw": RAW, "n_files": len(paths), "scen": SCEN}}


# ---------------------------------------------------------------- 观测器

def hops(cache):
    """log = J1 判出来的结算记录（带队列身份）；j2 = J2 声明的 id 流；另出链、覆盖、新入队。"""
    log, added, cover, chains = [], [], {}, []
    j2 = []
    for run, r in sorted(cache["runs"].items()):
        if r.get("scen") != SCEN:
            continue
        pk = r["pkts"]
        for t in set(p["turn"] for p in pk):
            cover[t] = cover.get(t, 0) + 1
        for i in range(1, len(pk)):
            prev, cur = pk[i - 1], pk[i]
            cp = collections.Counter((e["event_id"], e["story_id"]) for e in prev["q"])
            cc = collections.Counter((e["event_id"], e["story_id"]) for e in cur["q"])
            base = {
                "run": run, "card_id": r["card_id"], "run_chara": r["chara"],
                "file": cur["file"], "file_prev": prev["file"],
                "turn_panel": cur["turn"], "turn_prev_panel": prev["turn"],
                "turn_eng": (cur["turn"] - 1) if cur["turn"] is not None else None,
                "req_ch": (cur["req"] or {}).get("ch"),
                "req_event_id": (cur["req"] or {}).get("event_id"),
                "req_choice": (cur["req"] or {}).get("choice_number"),
                "same_turn": cur["turn"] == prev["turn"],
                "poll": cur["poll"] + prev["poll"],
                "ds": sum(b - a for a, b in zip(prev["five"], cur["five"])),
                "dm": sum(b - a for a, b in zip(prev["max"], cur["max"])),
                "dd": sum(b - a for a, b in zip(prev["dmax"], cur["dmax"])),
                "dvital": cur["vital"] - prev["vital"],
                "dmot": cur["mot"] - prev["mot"],
                "dfans": cur["fans"] - prev["fans"],
                "dsp": cur["sp"] - prev["sp"],
                "dnsk": cur["nsk"] - prev["nsk"],
            }
            fresh = []
            for (eid, sid), n in cp.items():
                k = n - cc.get((eid, sid), 0)
                for e in prev["q"]:
                    if e["event_id"] == eid and e["story_id"] == sid and k > 0:
                        log.append(dict(base, **e, n_dup=n))
                        k -= 1
            for (eid, sid), n in cc.items():
                k = n - cp.get((eid, sid), 0)
                for e in cur["q"]:
                    if e["event_id"] == eid and e["story_id"] == sid and k > 0:
                        added.append(dict(base, **e))
                        fresh.append(e["event_id"])
                        k -= 1
            if fresh and base["req_event_id"] is not None:
                chains.extend((base["req_event_id"], f) for f in fresh)
            if base["req_ch"] == "event" and base["req_event_id"] is not None:
                j2.append(base)
    return log, added, cover, chains, j2


def j_check(log, j2):
    """J1 与 J2 逐跳对账。"""
    byfile = {}
    for c in j2:
        byfile.setdefault((c["run"], c["file"]), []).append(c["req_event_id"])
    both = agree = only1 = 0
    ex = []
    for c in log:
        ids = byfile.get((c["run"], c["file"]), [])
        if not ids:
            only1 += 1
            continue
        both += 1
        if c["event_id"] in ids:
            agree += 1
        elif len(ex) < 6:
            ex.append({"file": c["file"], "j1": c["event_id"], "j2": ids,
                       "turn": c["turn_panel"]})
    return {"log_j1": len(log), "j2_hops": len(j2), "both": both, "agree": agree,
            "agree_rate": (agree / both) if both else None, "j1_without_j2": only1,
            "j2_hop_without_j1_removal": len(j2) - both, "disagree_examples": ex}


def classify(log):
    """按 (event_id, story_id) 聚合：局数、每局次数、干净效果跳与平均增量。"""
    per = {}
    for c in log:
        k = (c["event_id"], c["story_id"])
        a = per.setdefault(k, {"runs": set(), "n": 0, "per_run": collections.Counter(),
                               "turns": collections.Counter(), "eff_turns": set(),
                               "choices": collections.Counter(), "n_same": 0, "n_clean": 0,
                               "n_eff": 0, "fam": family(c["story_id"]),
                               "chara_id": c["chara_id"], "pt": c["play_timing"],
                               "n_choice": c["n_choice"], "succ": False,
                               "ds": 0, "dm": 0, "dd": 0, "dsp": 0, "dvital": 0, "dmot": 0,
                               "dfans": 0, "dnsk": 0})
        a["runs"].add(c["run"])
        a["n"] += 1
        a["turns"][c["turn_panel"]] += 1
        a["choices"][c["req_choice"]] += 1
        a["succ"] = a["succ"] or bool(c["succ"])
        if not c["same_turn"]:
            continue
        a["per_run"][c["run"]] += 1
        a["n_same"] += 1
        if c["poll"]:
            continue
        a["n_clean"] += 1
        d = (c["ds"], c["dm"], c["dd"], c["dsp"], c["dvital"], c["dmot"], c["dfans"], c["dnsk"])
        if any(d):
            a["n_eff"] += 1
            a["eff_turns"].add(c["turn_panel"])
        for key, val in zip(("ds", "dm", "dd", "dsp", "dvital", "dmot", "dfans", "dnsk"), d):
            a[key] += val
    out = {}
    for k, a in per.items():
        pr = sorted(a["per_run"].values()) or [0]
        nc = max(1, a["n_clean"])
        out[k] = dict(a, n_runs=len(a["runs"]), per_run_min=pr[0],
                      per_run_med=pr[len(pr) // 2], per_run_max=pr[-1],
                      n_cross=a["n"] - a["n_same"], n_polluted=a["n_same"] - a["n_clean"],
                      ds_avg=a["ds"] / nc, dm_avg=a["dm"] / nc, dd_avg=a["dd"] / nc,
                      dsp_avg=a["dsp"] / nc, dvital_avg=a["dvital"] / nc,
                      dmot_avg=a["dmot"] / nc, dfans_avg=a["dfans"] / nc,
                      dnsk_avg=a["dnsk"] / nc)
    return out


def by_turn(log, cover, consts):
    """回合日程：分母 = 覆盖到该面板回合的局数；同局同回合只算「该局这回合有结算」。"""
    no_ev = set(consts.get("no_event_turns") or [])
    at = collections.defaultdict(set)
    dec = collections.defaultdict(set)
    checks = collections.Counter()
    for c in log:
        at[(c["run"], c["turn_panel"])].add((c["event_id"], c["story_id"]))
        checks[c["turn_panel"]] += 1
        if c["n_choice"] >= 2:
            dec[c["turn_panel"]].add((c["run"], c["event_id"], c["story_id"]))
    runs_at, events_at = collections.Counter(), collections.Counter()
    for (_run, t), s in at.items():
        runs_at[t] += 1
        events_at[t] += len(s)
    rows = {}
    for t in sorted(set(cover) | set(runs_at)):
        if t is None:
            continue
        eng = t - 1
        rows[t] = {"turn_panel": t, "turn_eng": eng, "n_runs_cover": cover.get(t, 0),
                   "n_runs_with_event": runs_at.get(t, 0),
                   "n_distinct_events": events_at.get(t, 0),
                   "n_decision_events": len(dec.get(t, ())), "n_checks": checks.get(t, 0),
                   "in_no_event_turns": eng in no_ev}
    return rows


def master_check(cls):
    """身份 join 方向实测：event_id 只在 400015xxx 族等于 story_data.id。"""
    if not os.path.exists(MASTER):
        return {"available": False}
    db = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
    hit = collections.Counter()
    tot = collections.Counter()
    for (eid, sid) in cls:
        f = family(sid)
        tot[f] += 1
        row = db.execute("select story_id from single_mode_story_data where id=?",
                         (eid,)).fetchone()
        if row and row[0] == sid:
            hit[f] += 1
    n_rows, n_story = db.execute(
        "select count(*), count(distinct story_id) from single_mode_story_data "
        "where story_id like ?", (MATSURI_STORY + "%",)).fetchone()
    seen = {sid for (_e, sid) in cls if family(sid) == "剧本15自持"}
    return {"available": True, "by_family": {f: "%d/%d" % (hit.get(f, 0), tot.get(f, 0))
                                             for f in sorted(tot)},
            "mdb_400015_rows": n_rows, "mdb_400015_stories": n_story,
            "packet_400015_stories": len(seen)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    args = ap.parse_args()
    os.chdir(REPO)
    if args.from_cache and os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
    else:
        cache = scan()
        os.makedirs("out", exist_ok=True)
        json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    consts = json.load(open(os.path.join(REPO, "data", "constants.json"), encoding="utf-8"))

    log, added, cover, chains, j2 = hops(cache)
    runs15 = [r for r in cache["runs"].values() if r.get("scen") == SCEN]
    cls = classify(log)
    rows = by_turn(log, cover, consts)
    jc = j_check(log, j2)
    mc = master_check(cls)

    fam_n = collections.Counter()
    for c in log:
        fam_n[family(c["story_id"])] += 1

    lines = []
    w = lines.append
    w("#57 剧本自持事件表（一手报文，scen 15）")
    w("报文 %s 个 / 解码失败 %s；scen15 局数 %d；结算记录 %d 条；新入队 %d 条"
      % (cache["meta"]["n_files"], cache["stats"].get("decode_fail", 0), len(runs15),
         len(log), len(added)))
    tt = [k for k in cover if k is not None]
    w("每局结算次数 = %.1f；覆盖面板回合 %d 个（%d..%d）"
      % (len(log) / max(1, len(runs15)), len(cover), min(tt), max(tt)))
    w("身份族分布（按结算条数）：%s" % json.dumps(dict(fam_n), ensure_ascii=False))
    w("")
    w("## 1 J1（队列移除）vs J2（check_event 请求显式 id）对账")
    for k in ("log_j1", "j2_hops", "both", "agree", "agree_rate", "j1_without_j2",
              "j2_hop_without_j1_removal"):
        w("  %s = %s" % (k, jc[k]))
    if jc["disagree_examples"]:
        w("  不一致样例：%s" % json.dumps(jc["disagree_examples"], ensure_ascii=False))
    w("")
    w("## 2 队列条目字段 / play_timing")
    w("  字段直方图：%s" % json.dumps(cache["field_hist"], ensure_ascii=False))
    w("  play_timing：%s" % json.dumps(cache["play_timing"], ensure_ascii=False))
    only_id = collections.Counter()
    for (eid, sid) in cls:
        only_id[eid] += 1
    dup = {e: n for e, n in only_id.items() if n > 1}
    w("  只按 event_id 会并错：同一 id 挂多个 story_id 的 id 有 %d 个 %s"
      % (len(dup), dict(sorted(dup.items(), key=lambda kv: -kv[1])[:8])))
    w("")
    w("## 3 事件表（键 = (event_id, story_id)，按结算条数排序，前 45）")
    w("  %-8s %-11s %-6s %-4s %-10s %-4s %-11s %-9s %-7s %-7s %s" %
      ("event_id", "story_id", "chara", "pt", "族", "次数", "每局min/med/max",
       "干净/同回", "Δ五维", "Δ上限", "ΔPT / 回合众数 / 选项数"))
    for (eid, sid), a in sorted(cls.items(), key=lambda kv: -kv[1]["n"])[:45]:
        tm = a["turns"].most_common(1)[0] if a["turns"] else ("-", 0)
        w("  %-8s %-11s %-6s %-4s %-10s %-4d %-11s %-9s %-7.1f %-7.1f %.1f/t%s(×%d)/%d选" %
          (eid, sid, a["chara_id"], a["pt"], a["fam"], a["n"],
           "%d/%d/%d" % (a["per_run_min"], a["per_run_med"], a["per_run_max"]),
           "%d/%d" % (a["n_clean"], a["n_same"]), a["ds_avg"], a["dm_avg"], a["dsp_avg"],
           tm[0], tm[1], a["n_choice"]))
    w("")
    w("## 4 回合日程（面板轴；引擎 = 面板 − 1）")
    w("  %-4s %-4s %-6s %-8s %-7s %-8s %-8s %-6s %s" %
      ("面板", "引擎", "覆盖局", "有结算局", "触发率", "distinct", "带选项", "结算次",
       "no_event_turns(引擎轴)"))
    for t, r in rows.items():
        rate = r["n_runs_with_event"] / r["n_runs_cover"] if r["n_runs_cover"] else 0.0
        w("  %-4d %-4d %-6d %-8d %-7.2f %-8d %-8d %-6d %s" %
          (r["turn_panel"], r["turn_eng"], r["n_runs_cover"], r["n_runs_with_event"], rate,
           r["n_distinct_events"], r["n_decision_events"], r["n_checks"],
           "是" if r["in_no_event_turns"] else ""))
    w("")
    w("## 5 与基础库 no_event_turns 的分歧（面板回合，覆盖 ≥3 局）")
    hit = [r for r in rows.values() if r["n_runs_cover"] >= 3 and r["in_no_event_turns"]
           and r["n_runs_with_event"] > 0]
    miss = [r for r in rows.values() if r["n_runs_cover"] >= 3 and not r["in_no_event_turns"]
             and r["n_runs_with_event"] == 0]
    w("  库里写「这回合没事件」但真机有结算：%d 个：%s"
      % (len(hit), sorted(r["turn_panel"] for r in hit)))
    w("  库里允许事件但真机一次没有：%d 个：%s"
      % (len(miss), sorted(r["turn_panel"] for r in miss)))
    n_cov = sum(1 for r in rows.values() if r["n_runs_cover"] >= 3)
    n_lib = sum(1 for r in rows.values() if r["n_runs_cover"] >= 3 and r["in_no_event_turns"])
    n_obs = sum(1 for r in rows.values() if r["n_runs_cover"] >= 3 and r["n_runs_with_event"] > 0)
    w("  （覆盖 ≥3 局的 %d 个回合里：库说没事件 %d 个 / 实测有结算 %d 个）"
      % (n_cov, n_lib, n_obs))
    dec_turns = [r for r in rows.values() if r["n_runs_cover"] >= 3
                 and r["n_decision_events"] > 0]
    w("  带真实选项（选项数 ≥2）的结算发生在 %d 个回合：%s"
      % (len(dec_turns), sorted(r["turn_panel"] for r in dec_turns)))
    w("")
    w("## 6 事件链（消费 X 的同时新入队 Y；前 25 对）")
    for (x, y), n in collections.Counter(chains).most_common(25):
        w("  %-8s -> %-8s ×%d" % (x, y, n))
    w("")
    w("## 7 master.mdb 身份 join 方向（story_data.id 命中 / 该族 (event_id,story_id) 对数）")
    if mc.get("available"):
        w("  %s" % json.dumps(mc["by_family"], ensure_ascii=False))
        w("  剧本15自持族：master.mdb 里 %d 行 / %d 个 story_id；报文里出现过 %d 个"
          % (mc["mdb_400015_rows"], mc["mdb_400015_stories"], mc["packet_400015_stories"]))
    else:
        w("  master.mdb 不在位，跳过")

    txt = "\n".join(lines)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cw = csv.writer(f)
        cw.writerow(["event_id", "story_id", "family", "chara_id", "play_timing", "n_choice",
                     "succ_flag", "n_settled", "n_runs", "n_same_turn_hop", "n_cross_turn_hop",
                     "n_polluted_hop", "n_clean_hop", "n_effective_hop",
                     "per_run_min", "per_run_med", "per_run_max",
                     "ds_avg", "dm_avg", "dd_avg", "dsp_avg", "dvital_avg", "dmot_avg",
                     "dfans_avg", "dnsk_avg", "choices", "turns", "effective_turns"])
        for (eid, sid), a in sorted(cls.items(), key=lambda kv: -kv[1]["n"]):
            cw.writerow([eid, sid, a["fam"], a["chara_id"], a["pt"], a["n_choice"],
                         int(a["succ"]), a["n"], a["n_runs"], a["n_same"], a["n_cross"],
                         a["n_polluted"], a["n_clean"], a["n_eff"],
                         a["per_run_min"], a["per_run_med"], a["per_run_max"],
                         round(a["ds_avg"], 3), round(a["dm_avg"], 3), round(a["dd_avg"], 3),
                         round(a["dsp_avg"], 3), round(a["dvital_avg"], 3),
                         round(a["dmot_avg"], 3), round(a["dfans_avg"], 3),
                         round(a["dnsk_avg"], 3),
                         json.dumps({str(k): v for k, v in sorted(a["choices"].items())},
                                    ensure_ascii=False),
                         json.dumps({str(k): v for k, v in sorted(a["turns"].items())}),
                         json.dumps(sorted(a["eff_turns"]))])
    with open(OUT_TURN, "w", newline="", encoding="utf-8-sig") as f:
        cw = csv.writer(f)
        cw.writerow(["turn_panel", "turn_eng", "n_runs_cover", "n_runs_with_event",
                     "trigger_rate", "n_distinct_events", "n_decision_events", "n_checks",
                     "in_no_event_turns"])
        for t, r in rows.items():
            cw.writerow([r["turn_panel"], r["turn_eng"], r["n_runs_cover"],
                         r["n_runs_with_event"],
                         round(r["n_runs_with_event"] / r["n_runs_cover"], 4)
                         if r["n_runs_cover"] else "",
                         r["n_distinct_events"], r["n_decision_events"], r["n_checks"],
                         int(r["in_no_event_turns"])])
    print(txt)


if __name__ == "__main__":
    main()
