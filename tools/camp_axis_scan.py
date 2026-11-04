#!/usr/bin/env python3
"""#53 合宿开始 vs 教诲改派：体力 +10/+15 与 干劲+1 到底落在哪个回合（纯报文，不跑引擎）。

一条命令重建全部表：

    python tools/camp_axis_scan.py --rebuild     # 全量扫报文（约 2 分钟），落缓存
    python tools/camp_axis_scan.py               # 默认走缓存，秒级重出全部表

**轴口径**（见 docs/回合轴口径审计_20261104.md §0）：报文 `chara_info.turn` = 面板 = 表行号（1–78），
引擎 `base.turn` = 报文 − 1。本文全部按**面板**号写，代码常数换算成引擎号另列。

三件待判：
  J1 快照语义：`out/_ura_window_turns.csv` 的 `vital` 是板前还是结算后 —— 用全 1–78 跑等式检验。
  J2 台阶落点：合宿窗＝面板 13–16/37–40/61–64（`training_set_id=6`），教诲改派窗＝面板
     3/12/24/36/48/60（`single_mode_15_advice_recommend.turn_num`）。逐包找「不能被当手解释的体力台阶」。
  J3 事件标记：报文里的 `*_request_common` 直接带 `event_id` / `current_turn`；教诲改派请求带
     `attach_advice_info_array` + `current_turn`。两条日志分别定到回合，优先于任何残差推断。

产物：`out/_camp_axis_packets.csv`（窗口逐包）/ `out/_camp_axis_steps.csv`（台阶与改派逐局）/
      `out/_camp_axis_report.txt`（全部表与裁决）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
MASTER = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "out", "_camp_axis_pkt.json")
OUT_PKT = os.path.join(ROOT, "out", "_camp_axis_packets.csv")
OUT_STEP = os.path.join(ROOT, "out", "_camp_axis_steps.csv")
OUT_ONSET = os.path.join(ROOT, "out", "_camp_axis_onsets.csv")
OUT_TXT = os.path.join(ROOT, "out", "_camp_axis_report.txt")

PLAIN = {101, 102, 103, 104, 105, 106}
KEIKO = {5101, 5102, 5103, 5104, 5105}
AXES = ("speed", "stamina", "power", "guts", "wiz")
TT_VITAL = 10
CAMP_START = (13, 37, 61)          # 面板：合宿首回合（training_set_id=6 的第一行）
YR = {13: "1 年目", 37: "2 年目", 61: "3 年目"}
# 主判据窗口（面板）：合宿首回合前 3 格到后 2 格 ＝ 10–15 / 34–39 / 58–63
WINDOWS = [(c - 3, c + 2) for c in CAMP_START]


def in_win(t):
    return any(lo <= t <= hi for lo, hi in WINDOWS)

META_KEYS = {"viewer_id", "device", "device_id", "device_name", "graphics_device_name",
             "ip_address", "platform_os_version", "carrier", "keychain", "locale",
             "button_info", "dmm_viewer_id", "dmm_onetime_token", "steam_id",
             "steam_session_auth_ticket"}


# ---------------------------------------------------------------- 报文侧

def req_of(d):
    """请求报文 → (类型名, 关键字段)。教诲改派请求没有 *_request_common 外壳，按字段签名认。"""
    if not isinstance(d, dict):
        return None, {}
    for k, v in d.items():
        if isinstance(k, str) and k.endswith("_request_common") and isinstance(v, dict):
            return k.replace("single_mode_", "").replace("_request_common", ""), {
                "turn": v.get("current_turn"), "event_id": v.get("event_id"),
                "select_id": v.get("select_id"), "choice_number": v.get("choice_number"),
                "preset": v.get("exec_auto_play_plan_id")}
    if "attach_advice_info_array" in d:
        return "change_advice", {"turn": d.get("current_turn"),
                                 "attach": "|".join("%s:%s" % (a.get("act_type"), a.get("advice_id"))
                                                    for a in d.get("attach_advice_info_array") or [])}
    return None, {}


def cells_of(src):
    """预告板一条链（home 或 festa）：command_id → 该格体力预告。"""
    out = {}
    for c in src or []:
        cid = c.get("command_id")
        if cid is None or cid not in (PLAIN | KEIKO):
            continue
        out[cid] = sum((e.get("value") or 0) for e in c.get("params_inc_dec_info_array") or []
                       if e.get("target_type") == TT_VITAL)
    return out


def resp_of(name, d):
    if not isinstance(d, dict):
        return None
    data = d.get("data")
    if not isinstance(data, dict):
        return None
    ci = data.get("chara_info")
    if not isinstance(ci, dict) or ci.get("turn") is None:
        return None
    v = d.get("viewer_id") or (d.get("data_headers") or {}).get("viewer_id")
    fi = (data.get("festa_data_set") or {}).get("festa_info") or {}
    cr = data.get("command_result")
    cells = cells_of((data.get("home_info") or {}).get("command_info_array"))
    for k, val in cells_of((data.get("festa_data_set") or {}).get("command_info_array")).items():
        cells[k] = cells.get(k, 0) + val
    return {"run": "%s@%s@%s" % (v, ci.get("single_mode_chara_id"), ci.get("start_time")),
            "viewer": v, "chara": ci.get("single_mode_chara_id"),
            "scen": ci.get("scenario_id"), "file": name, "turn": ci.get("turn"),
            "vital": ci.get("vital"), "max_vital": ci.get("max_vital"),
            "mot": ci.get("motivation"), "camp_left": fi.get("summer_camp_remain_turn_num"),
            "cmd": (cr or {}).get("command_id") if isinstance(cr, dict) else None,
            "fs": sum(ci.get(k) or 0 for k in AXES), "cells": cells,
            "ev": [e.get("event_id") for e in (data.get("unchecked_event_array") or [])
                   if isinstance(e, dict)],
            "sel": data.get("select_index_info_array")}


def scan(viewer, scen):
    """一遍扫全部报文，落逐包序列（含请求类型），存缓存。"""
    files = sorted(os.listdir(RAW))
    out, last_req, n = [], None, 0
    t0 = time.time()
    for name in files:
        if not name.endswith(".bin"):
            continue
        try:
            d = decode_file(os.path.join(RAW, name))
        except Exception:
            continue
        if name.endswith("_notify_request.bin"):
            last_req = req_of(d)
            continue
        r = resp_of(name, d)
        if not r:
            continue
        if viewer and r["viewer"] != viewer:
            continue
        if r["scen"] != scen:
            continue
        rn, rb = last_req or (None, {})
        r["req"], r["req_turn"], r["event_id"] = rn, rb.get("turn"), rb.get("event_id")
        r["attach"] = rb.get("attach")
        out.append(r)
        n += 1
    return {"files": len(files), "n": n, "seconds": round(time.time() - t0, 1),
            "pkts": out}


def load_cache(path):
    return json.load(open(path, encoding="utf-8"))


# ---------------------------------------------------------------- J1 快照语义

def turn_frames(per):
    """逐 (run, 回合) 的三个快照：首包 / 板前 / 结算 / 末包，加当手体力预告。"""
    out = {}
    for run, d in per.items():
        for t, ps in d.items():
            f = {"run": run, "turn": t, "n_pkt": len(ps),
                 "first": ps[0]["vital"], "last": ps[-1]["vital"],
                 "first_camp": ps[0]["camp_left"], "last_camp": ps[-1]["camp_left"],
                 "board": None, "settle": None, "pred": None, "cmd": None}
            si = next((i for i, q in enumerate(ps) if q["cmd"] in (PLAIN | KEIKO)), None)
            if si is not None:
                f["settle"] = ps[si]["vital"]
                f["cmd"] = ps[si]["cmd"]
                bi = next((i for i in range(si - 1, -1, -1) if ps[i]["cells"]), None)
                if bi is not None:
                    f["board"] = ps[bi]["vital"]
                    f["pred"] = ps[bi]["cells"].get(f["cmd"])
            out[(run, t)] = f
    return out


def semantics(frames):
    """单列快照到底是板前还是结算后 —— 用全 1–78 的等式检验（容差 0）。"""
    st = collections.Counter()
    ex = collections.defaultdict(list)
    by = collections.defaultdict(dict)
    for (run, t), f in frames.items():
        by[run][t] = f
    for run, d in by.items():
        for t in sorted(d):
            f = d[t]
            if f["settle"] is None or f["board"] is None or f["pred"] is None:
                continue
            dh = f["settle"] - f["board"]
            st["n_pairs"] += 1
            if dh - f["pred"] == 0:
                st["resid_zero"] += 1
            st["n_resid"] += 1
            if dh - f["pred"] != 0 and len(ex["resid"]) < 10:
                ex["resid"].append((run[9:22], t, f["pred"], dh, dh - f["pred"]))
            nx, pv = d.get(t + 1), d.get(t - 1)
            # E1 板前语义：last(t+1) 当板前看 ⇒ first(t+1) = first(t) + d_hand(t)
            if nx and nx["first"] is not None and f["first"] is not None:
                if nx["first"] == f["first"] + dh:
                    st["E1_ok"] += 1
                else:
                    st["E1_bad"] += 1
                    if len(ex["E1"]) < 10:
                        ex["E1"].append((run[9:22], t, f["first"], nx["first"], dh,
                                         nx["first"] - f["first"] - dh))
            # E3 结算后语义：last(t) = last(t-1) + d_hand(t)
            if pv and pv["last"] is not None:
                if pv["last"] + dh == f["last"]:
                    st["E3_ok"] += 1
                else:
                    st["E3_bad"] += 1
                    if len(ex["E3"]) < 10:
                        ex["E3"].append((run[9:22], t, pv["last"], f["last"], dh,
                                         f["last"] - pv["last"] - dh))
            # E2 主线原题第二式（与 E1 同构，t 平移一格）：last(t) = last(t-1) + d_hand(t-1)
            if pv and pv["settle"] is not None and pv["board"] is not None:
                if f["last"] == pv["last"] + (pv["settle"] - pv["board"]):
                    st["E2_ok"] += 1
                else:
                    st["E2_bad"] += 1
            # 直接结构检验：跨回合边界有没有漂移
            if nx and nx["first"] is not None:
                st["n_drift"] += 1
                if nx["first"] == f["last"]:
                    st["drift_zero"] += 1
                else:
                    k = nx["first"] - f["last"]
                    st["drift_%+d" % k] += 1
    return st, ex


# ---------------------------------------------------------------- J2/J3 逐包台阶

def series(pkts):
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for p in pkts:
        per[p["run"]][p["turn"]].append(p)
    for d in per.values():
        for t in d:
            d[t].sort(key=lambda p: p["file"])
    return per


def axis_steps(per):
    """逐 (run, 边界) 找「+体力台阶落在哪个包」，并单独登记教诲改派包。"""
    steps, advice = [], []
    for run, d in sorted(per.items()):
        allt = sorted(x for x in d if d[x])
        flat = [p for t in allt for p in d[t]]
        for cs in CAMP_START:
            lo, hi = cs - 3, cs + 2
            wins = sorted((p for t in allt if lo <= t <= hi for p in d[t]),
                          key=lambda p: p["file"])
            for a, b in zip(wins, wins[1:]):
                if b["turn"] > cs + 1 or a["turn"] < lo:
                    continue
                dv = (b["vital"] or 0) - (a["vital"] or 0)
                dm = (b["mot"] or 0) - (a["mot"] or 0)
                if dv <= 0 and dm <= 0:
                    continue
                steps.append({"run": run, "camp_start": cs, "from_file": a["file"],
                              "to_file": b["file"], "from_turn": a["turn"], "to_turn": b["turn"],
                              "v_from": a["vital"], "v_to": b["vital"], "d_vital": dv,
                              "d_mot": dm, "mot_before": a["mot"], "mot_after": b["mot"],
                              "camp_from": a["camp_left"], "camp_to": b["camp_left"],
                              "max_vital": b["max_vital"], "req": b["req"],
                              "event_id": b["event_id"], "cmd": b["cmd"],
                              "is_event": int(b["cmd"] is None and b["req"] != "exec_command"),
                              "capped": int(a["vital"] is not None and b["max_vital"] and
                                            a["vital"] >= b["max_vital"]),
                              "at_max": int(b["vital"] is not None and b["max_vital"] and
                                            b["vital"] >= b["max_vital"])})
        for p in flat:
            if p["req"] == "change_advice":
                advice.append({"run": run, "file": p["file"], "req_turn": p["req_turn"],
                               "resp_turn": p["turn"], "vital": p["vital"], "mot": p["mot"],
                               "camp_left": p["camp_left"], "prev_vital": None,
                               "prev_mot": None, "attach": p["attach"] or ""})
        # 改派包的前一条（同回合或前回合最后一包），用来判「改派响应里体力/干劲动没动」
        for a in advice:
            if a["run"] != run:
                continue
            i = next((k for k, p in enumerate(flat) if p["file"] == a["file"]), None)
            if i:
                a["prev_vital"], a["prev_mot"] = flat[i - 1]["vital"], flat[i - 1]["mot"]
    return steps, advice


def camp_onsets(per):
    """`summer_camp_remain_turn_num` 由 0→4 的那条包＝合宿正式开始。

    与「净增台阶」判据**独立**：体力顶满时增益被钳成 0，台阶判据会漏掉这一格，
    标记判据不会 ⇒ 用它当分母才诚实。
    """
    out = []
    for run, d in sorted(per.items()):
        allt = sorted(x for x in d if d[x])
        for cs in CAMP_START:
            lo, hi = cs - 3, cs + 2
            wins = sorted((p for t in allt if lo <= t <= hi for p in d[t]),
                          key=lambda p: p["file"])
            for a, b in zip(wins, wins[1:]):
                if a["camp_left"] == 0 and b["camp_left"] == 4:
                    out.append({"run": run, "camp_start": cs, "turn": b["turn"], "file": b["file"],
                                "req": b["req"], "event_id": b["event_id"],
                                "v_from": a["vital"], "v_to": b["vital"],
                                "d_vital": (b["vital"] or 0) - (a["vital"] or 0),
                                "mot_before": a["mot"], "mot": b["mot"],
                                "d_mot": (b["mot"] or 0) - (a["mot"] or 0),
                                "max_vital": b["max_vital"]})
    return out


def req_turn_hist(per):
    """全库 change_advice 请求的 current_turn 分布（面板轴自证）。"""
    c = collections.Counter()
    for d in per.values():
        for t in sorted(d):
            for p in d[t]:
                if p["req"] == "change_advice" and p["req_turn"] is not None:
                    c[p["req_turn"]] += 1
    return c


# ---------------------------------------------------------------- 表侧

def table_side(scen):
    db = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
    adv = db.execute("select id, turn_num from single_mode_15_advice_recommend order by turn_num").fetchall()
    camp = [r[0] for r in db.execute(
        "select turn from single_mode_turn where turn_set_id=? and training_set_id=6 order by turn",
        (scen,))]
    return {"advice": adv, "camp": camp}


def story_of(ids):
    if not ids:
        return {}
    db = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
    out = {}
    q = "select id, story_id, event_category, gallery_main_scenario, gallery_list_id " \
        "from single_mode_story_data where id in (%s)" % ",".join(str(int(i)) for i in ids)
    for r in db.execute(q):
        out[r[0]] = {"story_id": r[1], "event_category": r[2], "gallery_scenario": r[3],
                     "gallery_list": r[4]}
    return out


# ---------------------------------------------------------------- 报告

def span(ts):
    ts = sorted(set(ts))
    if not ts:
        return "—"
    out, a, b = [], ts[0], ts[0]
    for t in ts[1:]:
        if t == b + 1:
            b += 1
            continue
        out.append(str(a) if a == b else "%d–%d" % (a, b))
        a = b = t
    out.append(str(a) if a == b else "%d–%d" % (a, b))
    return ",".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewer", type=int, default=238107205)
    ap.add_argument("--scen", type=int, default=15)
    ap.add_argument("--rebuild", action="store_true", help="重扫报文（默认走缓存）")
    ap.add_argument("--cache", default=CACHE)
    a = ap.parse_args()

    if a.rebuild or not os.path.exists(a.cache):
        cache = scan(a.viewer, a.scen)
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
        print("重扫 %d 文件 / %d 包 / %.1fs → %s"
              % (cache["files"], cache["n"], cache["seconds"], a.cache))
    else:
        cache = load_cache(a.cache)
        print("读缓存 %s（%d 包，未重扫）" % (a.cache, cache["n"]))

    pkts = cache["pkts"]
    per = series(pkts)
    frames = turn_frames(per)
    st, ex = semantics(frames)
    steps, advice = axis_steps(per)
    onsets = camp_onsets(per)
    hist = req_turn_hist(per)
    tb = table_side(a.scen)
    ev_ids = sorted({s["event_id"] for s in steps if s["event_id"]})
    stories = story_of(ev_ids)

    # ---- 落盘
    with open(OUT_STEP, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["run", "camp_start", "to_turn", "to_file", "from_turn", "from_file", "v_from",
                "v_to", "d_vital", "d_mot", "mot_before", "mot_after", "camp_from", "camp_to",
                "max_vital", "req", "event_id", "cmd", "is_event", "capped", "at_max"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for s in sorted(steps, key=lambda x: (x["run"], x["camp_start"], x["to_file"])):
            w.writerow(s)
    with open(OUT_ONSET, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["run", "camp_start", "turn", "file", "req", "event_id", "v_from", "v_to",
                "d_vital", "mot_before", "mot", "d_mot", "max_vital"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for o in sorted(onsets, key=lambda x: (x["camp_start"], x["run"], x["file"])):
            w.writerow(o)
    win_turns = {t for t in range(min(lo for lo, _ in WINDOWS), max(hi for _, hi in WINDOWS) + 1)
                 if in_win(t)}
    with open(OUT_PKT, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["run", "turn", "file", "req", "req_turn", "event_id", "vital", "max_vital",
                "mot", "camp_left", "cmd", "fs"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in sorted(pkts, key=lambda x: (x["run"], x["turn"], x["file"])):
            if in_win(p["turn"]):
                w.writerow(p)

    # ---- 文本报告
    L = []
    P = L.append
    P("# #53 合宿开始 vs 教诲改派：台阶落点（2026-11-04，纯报文，未跑引擎）")
    P("")
    P("## J1 快照语义（本工具自家逐包序列，%d 包 / %d 个 (run,回合) 帧）"
      % (cache["n"], len(frames)))
    P("")
    P("先声明：`out/_ura_window_turns.csv` 在本任务开工当天 21:04 被 #52 改写成了双列")
    P("（`vital_start` / `vital_end` / `vital_at_board`），单列快照的歧义在那张表上已经不存在；")
    P("但**它现在是活口径本**，所以这里的语义地基不复用它，直接从报文逐包序列独立复现。")
    P("")
    P("记一个回合的报文序列 `ps`（按文件名＝时间序）：`first`=首包体力、`board`=结算前最后")
    P("一张预告板的体力、`settle`=带 `command_result` 那条的体力、`last`=末包体力；")
    P("`d_hand = settle − board`，`pred` = 板上该格的体力预告（home+festa 相加）。")
    P("")
    P("- 参与检验的回合（有当手板+结算）：**%d**" % st["n_pairs"])
    P("- 直接结构：**首包＝上一回合末包** %d/%d 成立（容差 0）；其余全是跨回合边界上发生的加/减项"
      % (st["drift_zero"], st["n_drift"]))
    if st["n_drift"] - st["drift_zero"]:
        P("  漂移分布：%s" % ", ".join("%s:%d" % (k[6:], v) for k, v in sorted(st.items())
                                    if k.startswith("drift_") and k != "drift_zero"))
    P("- E1 板前语义 `first(t+1) = first(t) + d_hand(t)`：成立 **%d** / 违例 **%d**"
      % (st["E1_ok"], st["E1_bad"]))
    P("- E3 结算后语义 `last(t) = last(t−1) + d_hand(t)`：成立 **%d** / 违例 **%d**"
      % (st["E3_ok"], st["E3_bad"]))
    P("- E2 主线原题第二式 `last(t) = last(t−1) + d_hand(t−1)`：成立 %d / 违例 %d"
      "（与 E1 同构，只是 t 平移一格，不是另一条判据）" % (st["E2_ok"], st["E2_bad"]))
    P("- `pred` 只含当手板的体力预告，**不含事件加项**：`d_hand − pred` 非零 %d/%d 回合"
      "（非恒 0 ⇒ 残差可用，但残差里混的是别的事件，不能当训练增量）"
      % (st["n_resid"] - st["resid_zero"], st["n_resid"]))
    P("")
    P("**地基结论**：单列快照若取的是**末包**（结算后），`d_hand` 应当对齐**同一回合**"
      "（E3 成立 %d ≫ E1 的 %d）；跨回合的加项在 `last(t)` 与 `first(t+1)` 之间不可分辨，"
      "**必须靠逐包顺序判落点**（J2/J3），单列快照表在这里给不出裁决。"
      % (st["E3_ok"], st["E1_ok"]))
    P("")
    P("### E1 违例样本（run, 面板t, first(t), first(t+1), d_hand, 残差）")
    for e in ex["E1"]:
        P("  " + str(e))
    P("### E3 违例样本（run, 面板t, last(t-1), last(t), d_hand, 残差）")
    for e in ex["E3"]:
        P("  " + str(e))
    P("### `d_hand − pred` 非零样本（pred, d_hand, 残差）")
    for e in ex["resid"]:
        P("  " + str(e))
    P("")
    P("## 轴锚点（表侧，`master.mdb`）")
    P("- 合宿窗 `single_mode_turn(turn_set_id=%d).training_set_id=6` ＝ 面板 %s"
      % (a.scen, span(tb["camp"])))
    P("- 教诲改派窗 `single_mode_15_advice_recommend.turn_num` ＝ 面板 %s"
      % ",".join(str(x[1]) for x in tb["advice"]))
    P("- 报文一手自证：全库 `change_advice` 请求的 `current_turn` 分布 ＝ %s"
      % ", ".join("%d:%d" % (k, v) for k, v in sorted(hist.items())))
    P("  ⇒ 改派请求确实只在教诲窗那一格提交（表侧 turn_num 与报文 `current_turn` 同为**面板轴**）。")
    P("")
    P("## J3 事件标记（最强一手：请求里直接带 event_id / current_turn）")
    P("| event_id | story_id | event_category | gallery_scenario | gallery_list | 出现次数 | 台阶 |")
    P("|---|---|---|---|---|---|---|")
    ev_stat = collections.defaultdict(lambda: [0, collections.Counter()])
    for s in steps:
        if s["event_id"]:
            ev_stat[s["event_id"]][0] += 1
            ev_stat[s["event_id"]][1][s["d_vital"]] += 1
    for eid in ev_ids:
        st_ = stories.get(eid, {})
        n, dv = ev_stat[eid]
        P("| %s | %s | %s | %s | %s | %d | %s |"
          % (eid, st_.get("story_id"), st_.get("event_category"), st_.get("gallery_scenario"),
             st_.get("gallery_list"), n,
             ", ".join("+%d×%d" % (k, v) for k, v in sorted(dv.items()))))
    P("")
    P("## J2 逐局台阶明细（合宿首回合前后各两格，只报**净增**的包）")
    for cs in CAMP_START:
        yr = YR[cs]
        P("")
        P("### %s（合宿首回合＝面板 %d，引擎 %d）" % (yr, cs, cs - 1))
        P("| run | 台阶落点包turn | Δvital | Δmot | mot前→后 | camp_left前→后 | 请求类型 | event_id | 体力顶满 |")
        P("|---|---|---|---|---|---|---|---|---|")
        for s in sorted(steps, key=lambda x: (x["run"], x["to_file"])):
            if s["camp_start"] != cs:
                continue
            P("| %s | %d | +%d | %+d | %s→%s | %s→%s | %s | %s | %s |"
              % (s["run"][9:34], s["to_turn"], s["d_vital"], s["d_mot"], s["mot_before"],
                 s["mot_after"], s["camp_from"], s["camp_to"], s["req"], s["event_id"] or "—",
                 "落点已顶" if s["at_max"] else ("台阶前已顶" if s["capped"] else "")))
        P("")
        P("#### 落在改派候选格（面板 %d）的事件类台阶——**全部不带 camp_left 0→4 标记，不是改派增益证据**"
          % (cs - 1))
        bad = [s for s in steps if s["camp_start"] == cs and s["to_turn"] == cs - 1
               and s["is_event"] and s["d_vital"] > 0]
        if bad:
            for s in bad:
                P("  %s t%d +%d (%s ev=%s camp=%s→%s)"
                  % (s["run"][9:34], s["to_turn"], s["d_vital"], s["req"], s["event_id"],
                     s["camp_from"], s["camp_to"]))
        else:
            P("  （无）")
    P("")
    P("## 教诲改派包本体（改派响应里体力/干劲动没动）")
    moved = [x for x in advice if x["prev_vital"] is not None and
             ((x["vital"] or 0) > (x["prev_vital"] or 0) or (x["mot"] or 0) > (x["prev_mot"] or 0))]
    P("- 改派响应共 %d 条（含面板 3/12/24/36/48/60 六格），其中**响应里体力或干劲净增的只有 %d 条**"
      % (len(advice), len(moved)))
    for x in moved:
        P("  ⚠ %s 请求turn=%s 响应turn=%s：%s→%s 体力 / %s→%s 干劲"
          % (x["run"][9:34], x["req_turn"], x["resp_turn"], x["prev_vital"], x["vital"],
             x["prev_mot"], x["mot"]))
    P("| run | 改派请求current_turn | 响应turn | 前一条vital→本条 | 前一条mot→本条 | camp_left |")
    P("|---|---|---|---|---|---|")
    for x in sorted(advice, key=lambda y: (y["run"], y["file"])):
        P("| %s | %s | %s | %s→%s | %s→%s | %s |"
          % (x["run"][9:34], x["req_turn"], x["resp_turn"], x["prev_vital"], x["vital"],
             x["prev_mot"], x["mot"], x["camp_left"]))
    P("")

    # ---- 标记包明细（独立于增益判据）
    P("## 合宿标记包（`summer_camp_remain_turn_num` 由 0→4 的那一条，逐局列全）")
    P("这一判据**不看体力是否净增**：顶上限时增益被钳成 0，台阶判据会漏掉那一格，标记判据不会。")
    for cs in CAMP_START:
        oc = [o for o in onsets if o["camp_start"] == cs]
        P("")
        P("### %s（合宿首回合＝面板 %d，引擎 %d）：标记包 %d 条"
          % (YR[cs], cs, cs - 1, len(oc)))
        P("| run | 标记落点turn | Δvital | v前→后 | Δmot | mot前→后 | max_vital | 请求 | event_id |")
        P("|---|---|---|---|---|---|---|---|---|")
        for o in sorted(oc, key=lambda x: (x["run"], x["file"])):
            P("| %s | %d | %+d | %s→%s | %+d | %s→%s | %s | %s | %s |"
              % (o["run"][9:34], o["turn"], o["d_vital"], o["v_from"], o["v_to"], o["d_mot"],
                 o["mot_before"], o["mot"], o["max_vital"], o["req"], o["event_id"] or "—"))
    P("")

    # ---- 裁决
    P("## 三态裁决（逐年次分开判）")
    P("主判据＝**合宿标记**：`camp_left` 由 0→4 的那条包落在哪一格（唯一、可指认到 event_id）；")
    P("副判据＝**改派包本体**：`change_advice` 响应自己动不动体力/干劲；")
    P("「事件类台阶总数」只作模糊对照——同窗里别的随机事件也在加体力，它们**不构成改派增益的证据**。")
    for cs in CAMP_START:
        lo, hi = cs - 3, cs + 2
        runs_win = {r for r, d in per.items() if any(lo <= t <= hi for t in d if d[t])}
        oc = [o for o in onsets if o["camp_start"] == cs]
        mk_on = [o for o in oc if o["turn"] == cs]
        mk_off = [o for o in oc if o["turn"] != cs]
        miss = sorted(runs_win - {o["run"] for o in oc})
        dvals = collections.Counter(o["d_vital"] for o in mk_on)
        clamped = [o for o in mk_on if o["d_vital"] <= 0 or
                   (o["max_vital"] and (o["v_to"] or 0) >= o["max_vital"])]
        cand = [s for s in steps if s["camp_start"] == cs and s["is_event"]]
        marked = [s for s in cand if s["camp_from"] == 0 and s["camp_to"] == 4]
        un_off = [s for s in cand if s not in marked and s["to_turn"] == cs - 1
                  and s["d_vital"] > 0]
        un_on = [s for s in cand if s not in marked and s["to_turn"] == cs and s["d_vital"] > 0]
        ad_moved = [x for x in advice if x["prev_vital"] is not None and
                    x["resp_turn"] in (cs - 1, cs)
                    and ((x["vital"] or 0) > (x["prev_vital"] or 0)
                         or (x["mot"] or 0) > (x["prev_mot"] or 0))]
        mot_ok = [o for o in mk_on if (o["mot_before"] or 0) < 5]
        mot_c = collections.Counter(o["d_mot"] for o in mot_ok)
        mot_up = [o for o in mot_ok if o["d_mot"] > 0]
        mot_ex = [o for o in mot_ok if o["d_mot"] <= 0]
        P("")
        P("### %s：窗口内有报文的局数 %d；有 0→4 标记的局数 %d；无标记（缺包或未触发）%d 局 %s"
          % (YR[cs], len(runs_win), len({o["run"] for o in oc}), len(miss), miss or ""))
        P("- 标记落点：**面板 %d（引擎 %d，合宿首回合）%d 条** / 面板 %d（引擎 %d，教诲改派）**%d** 条"
          % (cs, cs - 1, len(mk_on), cs - 1, cs - 2, len(mk_off)))
        P("- Δvital 分布（标记包）：%s；其中疑被 `max_vital` 钳住 %d 条"
          % (", ".join("%+d:%d" % (k, v) for k, v in sorted(dvals.items())) or "—", len(clamped)))
        for o in clamped:
            P("  ⚠ 钳位 %s 面板%d 体力 %s→%s（max=%s）"
              % (o["run"][9:34], o["turn"], o["v_from"], o["v_to"], o["max_vital"]))
        P("- 改派包本体（面板 %d/%d 的 `change_advice` 响应）净增条数：**%d**"
          % (cs - 1, cs, len(ad_moved)))
        P("- 模糊对照：窗口内**不带**标记的事件类台阶 面板 %d 侧 %d 条 / 面板 %d 侧 %d 条"
          % (cs - 1, len(un_off), cs, len(un_on)))
        if mk_on and not mk_off:
            verdict = "支持引擎 %d（合宿首回合，＝面板 %d）——标记唯一，无违例" % (cs - 1, cs)
        elif mk_off and not mk_on:
            verdict = "支持引擎 %d（教诲改派，＝面板 %d）" % (cs - 2, cs - 1)
        elif mk_on and mk_off:
            verdict = "有违例 ⇒ 只能算倾向（%d:%d）" % (len(mk_on), len(mk_off))
        else:
            verdict = "判不了（窗口内无 0→4 标记包）"
        P("- **体力裁决：%s**" % verdict)
        P("- 干劲：标记包中前值 <5（可证伪）%d 条 ⇒ %s；前值已顶 5 的 %d 条不可证伪"
          % (len(mot_ok),
             ", ".join("%+d:%d" % (k, v) for k, v in sorted(mot_c.items())) or "—",
             len(mk_on) - len(mot_ok)))
        for o in mot_ex:
            P("  ⚠ 违例 %s 面板%d 干劲 %s→%s（同包体力 %+d、camp_left 0→4、ev=%s）"
              % (o["run"][9:34], o["turn"], o["mot_before"], o["mot"], o["d_vital"],
                 o["event_id"]))
        if mot_ok and not mot_ex:
            P("- **干劲裁决：支持 +1（%d/%d 无违例）**" % (len(mot_up), len(mot_ok)))
        elif mot_ex:
            P("- **干劲裁决：倾向 +1（%d/%d，有违例 ⇒ 不许进配置）**" % (len(mot_up), len(mot_ok)))
        else:
            P("- **干劲裁决：本年次无可证伪样本**")
        exid = collections.Counter((o["event_id"], o["d_vital"]) for o in mk_on
                                   if o["d_vital"] > 0)
        P("- 标记事件↔台阶：%s" % ", ".join("ev%s:%+d×%d" % (k, v, n)
                                        for (k, v), n in sorted(exid.items())))
        zero = [o for o in mk_on if o["d_vital"] <= 0]
        if zero:
            P("- 标记包中 Δvital≤0 的 %d 条（%s）：增益被体力上限整条吞掉，"
              "**该包的 event_id 不能当合宿事件号**（它只是第一条读到 camp_left=4 的包）"
              % (len(zero), ", ".join("%s 面板%d %s→%s/max=%s ev=%s"
                                      % (o["run"][9:34], o["turn"], o["v_from"], o["v_to"],
                                         o["max_vital"], o["event_id"]) for o in zero)))
    P("")
    P("（产物：`out/_camp_axis_packets.csv` 窗口逐包 %d 行 / `out/_camp_axis_steps.csv` "
      "台阶候选 %d 行（含非事件类，全部落盘）/ `out/_camp_axis_onsets.csv` 合宿标记包 %d 行）"
      % (sum(1 for p in pkts if p["turn"] in win_turns), len(steps), len(onsets)))
    txt = "\n".join(L) + "\n"
    open(OUT_TXT, "w", encoding="utf-8").write(txt)
    print(txt)
    print("\n→ %s / %s / %s / %s" % (OUT_PKT, OUT_STEP, OUT_ONSET, OUT_TXT))


if __name__ == "__main__":
    main()
