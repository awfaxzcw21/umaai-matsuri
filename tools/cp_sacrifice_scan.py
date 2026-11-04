#!/usr/bin/env python3
"""#50 真机到底有没有「发表会兜底」——纯报文测量，不跑引擎。

引擎侧 `crates/matsuri-cli/src/searcharm.rs` 的 `forced_target`（检查点前把落子强制改成
「该场未达标演目」的稽古格）在 #48 / #49 两批配对 CRN 里都是**净损失**；但按本仓机制真实性
铁律，「分低」不能给一条**语义未证**的规则判死刑。这里用一手报文回答：真机玩家（含游戏自带的
自動プラン）在发表会前是否系统偏向未达标演目格。

    python tools/cp_sacrifice_scan.py                 # 全量扫报文 → 缓存 + CSV + 全部表
    python tools/cp_sacrifice_scan.py --from-cache    # 复用上次缓存，只重出表（秒级）
    python tools/cp_sacrifice_scan.py --windows 3,5 --perm 20000 --seed 20261104
    python tools/cp_sacrifice_scan.py --limit 2500    # 冒烟（产物走 *_smoke_*，不覆盖留档）

判据（**先写死再跑**，正文见 docs/真机兜底语义普查_20261104.md §1）：
  · 场次与 turn 出自报文 `festa_data_set.check_point_result_array`（首次出现该 schedule_id 的
    turn = 该场的真机 turn），**不照引擎的 t23/47/67/77 硬套**；need_act_level 出自 master.mdb
    `single_mode_15_schedule`（表侧优先）。
  · 一手 = `single_mode_exec_command_request_common`（command_type==1）＋其紧邻响应报文；
    选择时的盘面 = 该请求之前**最后一条带训练格的 board 快照**（不是事后的那张）。
  · 需求集 N(t) = {演目 i 的稽古格 : 选择时 act_level[i] < 该场 need_act_level}。
  · 窗口 = [T−W, T−1]；对照 = 同局、上一场之后、t ≤ T−W−1；两者都剔 camp 手。
  · **供给与选择分开算**（否则「窗口里全是稽古」会被当成偏好，而它其实只是系统把格子放上了板）：
      供给 O1 = 板上有稽古格的比例；O2 = 板上有「需求」稽古格（该演目未达标）的比例。
      选择 M1c = 稽古格在场时取了稽古的手占比；M3c = 需求格在场时落在需求格；
                M5 = 有稽古格但**没有**需求格时取稽古（M3c 的差分对照，用来排除
                    「稽古本来就值」这条混淆）。
      描述 M2 = 全部训练手落需求格的比例；M4 = 落点缺口 (need−Lv) − 板上稽古格平均缺口
                （只算板上 ≥2 稽古格的手，负 = 专挑最缺的）。
  · 主判据 = **2×2 差分（DiD）**：行=窗口/对照，列=有无需求稽古格在场，值=取稽古率。
      兜底成立 ⇒ 玩家只在「需求格在场」时才改主 ⇒ DiD > 0；若两列同涨 ⇒ DiD ≈ 0，
      说明只是「稽古本身值钱」，不是发表会触发的改写。
  · W = 3 与 5 **两档都跑并列报**，不挑窗口凑显著；另给**距离曲线**（dt=1..18）这个
    与窗口宽度无关的形状读数。
  · 检验：(a) (run,场) 层内 turn 标签置换（非正态，DiD 与每个比例各测一次，次数与种子写死）；
    (b) 层/run 级配对差 + 本仓 `tools/forced_cp_ci.py` 的 `T975` 表 t 区间；(c) 精确符号检验。
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import os
import random
import sqlite3
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402  sort_key / get_ci
from forced_cp_ci import t975  # noqa: E402  本仓统一 t 值表（df>60 才退化成 1.960）

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
CACHE = "out/_cp_sacrifice_cache.json"
OUT_CSV = "out/_cp_sacrifice_hands.csv"
OUT_TXT = "out/_cp_sacrifice_report.txt"
SUMMARY = "out/_cp_sacrifice_summary.json"
PLAIN = {101: "速", 102: "力", 103: "根", 105: "耐", 106: "智"}
KEIKO = {5101: 1, 5102: 2, 5103: 3, 5104: 4, 5105: 5}
ACT2CMD = {v: k for k, v in KEIKO.items()}
TIER = {1: "成功", 2: "大成功"}
PAIR_GAP = 3          # 「连续两手」允许的回合间隔（比赛/外出会占回合）
MAX_DT = 18           # 距离曲线的最远距离（引擎兜底闸门是 15 回合，这里多留 3 格看形状）


# ---------------------------------------------------------------- 静态表

def need_levels(mdb):
    """master.mdb → {schedule_id: (turn_num, need_act_num, need_act_level)}。"""
    c = sqlite3.connect("file:%s?mode=ro" % mdb.replace("\\", "/"), uri=True)
    cols = [r[1] for r in c.execute("pragma table_info(single_mode_15_schedule)")]
    out = {}
    for row in c.execute("select * from single_mode_15_schedule"):
        d = dict(zip(cols, row))
        out[d["id"]] = (d["turn_num"], d["need_act_num"], d["need_act_level"])
    c.close()
    return out


# ---------------------------------------------------------------- 扫描

def snap_of(data, top=None):
    """一条响应 → 盘面快照（训练格 + 演目状态 + camp + 已结算场）；无训练格则 None。"""
    ci = PS.get_ci(data)
    if not isinstance(ci, dict) or ci.get("turn") is None:
        return None
    fds = data.get("festa_data_set") or {}
    fi = fds.get("festa_info") or {}
    cells = {}
    for c in ((data.get("home_info") or {}).get("command_info_array") or []):
        cid = c.get("command_id")
        if cid in PLAIN or cid in KEIKO:
            cells[cid] = {"level": c.get("level"),
                          "heads": len(c.get("training_partner_array") or []),
                          "sub": len(c.get("sub_command_partner_array") or []),
                          "enable": c.get("is_enable")}
    if not cells:
        return None
    acts = {}
    for a in (fds.get("act_info_array") or []):
        acts[a.get("act_type")] = {"lv": a.get("level"), "gauge": a.get("act_gauge"),
                                   "need_gauge": a.get("need_act_gauge"),
                                   "state": a.get("act_training_state")}
    v = (data.get("viewer_id") or (top or {}).get("viewer_id")
         or ((top or {}).get("data_headers") or {}).get("viewer_id")
         or (data.get("data_headers") or {}).get("viewer_id"))
    return {"viewer": v, "chara": ci.get("single_mode_chara_id"),
            "start": ci.get("start_time"), "turn": ci.get("turn"),
            "cells": cells, "acts": acts,
            "camp": fi.get("summer_camp_remain_turn_num"),
            "step": fi.get("act_training_step"), "max_act_lv": fi.get("max_act_level"),
            "cp": {r.get("schedule_id"): r.get("result_type")
                   for r in (fds.get("check_point_result_array") or []) if isinstance(r, dict)}}


def scan(args):
    """单遍扫报文：请求/响应按序号配对；盘面冻结「请求前最后一条快照」；跨局串台用响应身份剔除。"""
    paths = sorted((os.path.join(RAW, f) for f in os.listdir(RAW) if f.endswith(".bin")),
                   key=PS.sort_key)
    if args.limit:
        paths = paths[:args.limit]
    hands, cps = [], collections.defaultdict(dict)
    cur = None
    stats = collections.Counter()
    mismatch = []
    for i, p in enumerate(paths):
        name = os.path.basename(p)
        stats["files"] += 1
        try:
            d = ml.decode_file(p)
        except Exception:  # noqa: BLE001
            stats["decode_fail"] += 1
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_notify_request.bin"):
            body = d.get("single_mode_exec_command_request_common")
            if not isinstance(body, dict):
                continue
            stats["exec_req"] += 1
            if body.get("command_type") != 1:
                continue
            stats["train_req"] += 1
            if cur is None:
                stats["no_board"] += 1
                continue
            resp = None
            if i + 1 < len(paths) and os.path.basename(paths[i + 1]).endswith("_notify_response.bin"):
                try:
                    resp = ml.decode_file(paths[i + 1])
                except Exception:  # noqa: BLE001
                    resp = None
            rd = (resp or {}).get("data") if isinstance(resp, dict) else None
            cr = (rd or {}).get("command_result") if isinstance(rd, dict) else None
            post = snap_of(rd, resp) if isinstance(rd, dict) else None
            if post and (post["viewer"], post["chara"], post["start"]) != \
                    (cur["viewer"], cur["chara"], cur["start"]):
                stats["run_cross"] += 1        # 冻结的板属于另一局 ⇒ 丢弃
                continue
            if body.get("current_turn") != cur["turn"]:
                mismatch.append([name, cur["turn"], body.get("current_turn")])
                stats["turn_mismatch"] += 1
            if not post:
                stats["no_resp"] += 1
            hands.append({"file": name,
                          "run": "%s@%s@%s" % (cur["viewer"], cur["chara"], cur["start"]),
                          "turn": body.get("current_turn"), "cell": body.get("command_id"),
                          "auto_plan": body.get("exec_auto_play_plan_id"),
                          "vital": body.get("current_vital"),
                          "state": cr.get("result_state") if isinstance(cr, dict) else None,
                          "camp": cur["camp"], "step": cur["step"], "max_act_lv": cur["max_act_lv"],
                          "acts": cur["acts"], "cells": cur["cells"], "cp": cur["cp"],
                          "post_acts": (post or {}).get("acts")})
            if post:
                cur = post
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        s = snap_of(data, d)
        if s:
            cur = s
            r = "%s@%s@%s" % (s["viewer"], s["chara"], s["start"])
            for sid, tier in (s["cp"] or {}).items():
                e = cps[r].get(sid)
                if e is None:
                    cps[r][sid] = {"turn": s["turn"], "tier": tier, "first_file": name}
                else:
                    e["tier"] = tier
                stats["cp_seen"] += 1
    return {"stats": dict(stats), "hands": hands,
            "cps": {k: {str(sid): v for sid, v in c.items()} for k, c in cps.items()},
            "turn_mismatch": mismatch[:20], "n_mismatch": len(mismatch), "argv": vars(args)}


# ---------------------------------------------------------------- 判据层

def _intkeys(d):
    """JSON 往返会把 dict 的 int 键变成字符串；统一还原成 int 键（否则 ACT2CMD/KEIKO 查不到）。"""
    out = {}
    for k, v in (d or {}).items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            out[k] = v
    return out


def build_rows(cache, need):
    """原始手 → 带 kind / camp / 该场 need / 需求集 / M2~M4 取值域的分析行。"""
    cps = {k: {int(sid): v for sid, v in c.items()} for k, c in cache["cps"].items()}
    sched = {r: sorted((v["turn"], sid) for sid, v in c.items()) for r, c in cps.items()}
    rows = []
    for h in cache["hands"]:
        cell = h["cell"]
        kind = "keiko" if cell in KEIKO else ("plain" if cell in PLAIN else None)
        if kind is None:
            continue
        acts = _intkeys(h["acts"])
        lvs = {a: (v or {}).get("lv") for a, v in acts.items()}
        r = {"run": h["run"], "turn": h["turn"], "cell": cell, "kind": kind, "file": h["file"],
             "auto": bool(h["auto_plan"]), "plan": h["auto_plan"], "state": h["state"],
             "camp": bool(h["camp"]), "step": h["step"], "vital": h["vital"],
             "lvs": lvs, "gauges": {a: (v or {}).get("gauge") for a, v in acts.items()},
             "a_states": {a: (v or {}).get("state") for a, v in acts.items()},
             "board": sorted(_intkeys(h["cells"])), "sched": sched.get(h["run"]) or [],
             "dlv": {}}
        for a, v in _intkeys(h.get("post_acts")).items():
            prev = (acts.get(a) or {}).get("lv")
            if prev is not None and isinstance(v, dict) and v.get("lv") is not None:
                r["dlv"][a] = v["lv"] - prev
        nxt = [(t, sid) for t, sid in r["sched"] if t > r["turn"]]
        r["T"], r["sid"] = (nxt[0] if nxt else (None, None))
        r["need"] = need[r["sid"]][2] if r["sid"] in need else None
        r["prev_T"] = max([t for t, _ in r["sched"] if t < r["T"]], default=1) if r["T"] else None
        nd = r["need"] or 0
        r["low"] = sorted(a for a, lv in r["lvs"].items() if lv is not None and lv < nd)
        r["nlow"] = len(r["low"])
        nc = {ACT2CMD[a] for a in r["low"]}
        r["needcells"] = sorted(nc)
        r["kb"] = sorted(c for c in r["board"] if c in KEIKO)          # 板上稽古格（非 camp 常只有 1 格）
        r["kn_need"] = sorted(c for c in r["kb"] if c in nc)            # 板上「需求」稽古格
        r["m2_den"] = r["nlow"] > 0
        r["m2_val"] = 1.0 if cell in nc else 0.0
        # 玩家真正的选择面在「稽古格有没有在场」这一层（供给 O1/O2 → 选择 M1c/M3c）
        r["o1_den"] = True
        r["o1_val"] = 1.0 if r["kb"] else 0.0
        r["o2_den"] = True
        r["o2_val"] = 1.0 if r["kn_need"] else 0.0
        r["m1c_den"] = bool(r["kb"])
        r["m1c_val"] = 1.0 if r["kind"] == "keiko" else 0.0
        r["m3c_den"] = bool(r["kn_need"])
        r["m3c_val"] = 1.0 if cell in nc else 0.0
        # 差分对照：板上有稽古格但**没有需求格**时照样取不取稽古（排除「稽古本来就值」这条混淆）
        r["m5_den"] = bool(r["kb"]) and not r["kn_need"]
        r["m5_val"] = 1.0 if r["kind"] == "keiko" else 0.0
        r["m3_base"] = (1.0 * len(r["kn_need"]) / len(r["kb"])) if r["kb"] else None
        gaps = [max(0, nd - (r["lvs"].get(KEIKO[c]) or 0)) for c in r["kb"]]
        r["m4_den"] = r["kind"] == "keiko" and len(r["kb"]) >= 2 and r["need"] is not None
        r["m4_val"] = ((max(0, nd - (r["lvs"].get(KEIKO[cell]) or 0)) - sum(gaps) / len(gaps))
                       if r["m4_den"] else None)
        r["chosen_gap"] = max(0, nd - (r["lvs"].get(KEIKO[cell]) or 0)) if kind == "keiko" else None
        rows.append(r)
    return rows, cps


def side(r, W):
    if r["T"] is None or r["need"] is None:
        return None
    if r["T"] - W <= r["turn"] <= r["T"] - 1:
        return "win"
    if r["prev_T"] <= r["turn"] <= r["T"] - W - 1:
        return "ctl"
    return None


def stratify(rows, W, camp="out", manual=None, kind=None):
    st = collections.defaultdict(lambda: {"win": [], "ctl": []})
    for r in rows:
        if camp == "out" and r["camp"]:
            continue
        if camp == "only" and not r["camp"]:
            continue
        if manual is True and r["auto"]:
            continue
        if manual is False and not r["auto"]:
            continue
        if kind and r["kind"] != kind:
            continue
        sd = side(r, W)
        if sd:
            st[(r["run"], r["sid"])][sd].append(r)
    return st


METRICS = {
    "O1": ("o1", lambda r: True, lambda r: r["o1_val"]),
    "O2": ("o2", lambda r: True, lambda r: r["o2_val"]),
    "M1": ("m1", lambda r: True, lambda r: 1.0 if r["kind"] == "keiko" else 0.0),
    "M1c": ("m1c", lambda r: r["m1c_den"], lambda r: r["m1c_val"]),
    "M2": ("m2", lambda r: r["m2_den"], lambda r: r["m2_val"]),
    "M3c": ("m3c", lambda r: r["m3c_den"], lambda r: r["m3c_val"]),
    "M5": ("m5", lambda r: r["m5_den"], lambda r: r["m5_val"]),
    "M4": ("m4", lambda r: r["m4_den"], lambda r: r["m4_val"]),
}
PCT = ("O1", "O2", "M1", "M1c", "M2", "M3c", "M5")      # 比例型（pp）；M4 是 Lv 格


def stat(lst, mkey):
    _, den, val = METRICS[mkey]
    v = [val(r) for r in lst if den(r) and val(r) is not None]
    if not v:
        return None
    return {"n": len(v), "k": sum(v), "rate": sum(v) / len(v)}


def ci(vals):
    v = [x for x in vals if x is not None and not math.isnan(x)]
    n = len(v)
    if n < 2:
        return (v[0] if v else float("nan"), (float("nan"), float("nan")), n)
    m = sum(v) / n
    s = math.sqrt(sum((x - m) ** 2 for x in v) / (n - 1))
    h = t975(n - 1) * s / math.sqrt(n)
    return m, (m - h, m + h), n


def sign_p(pos, tot):
    if tot == 0:
        return float("nan")
    k = min(pos, tot - pos)
    return min(1.0, 2.0 * sum(math.comb(tot, i) for i in range(k + 1)) / (2.0 ** tot))


def perm_test(st, mkey, B, seed):
    """(run,场) 层内 turn 标签置换：层内窗口手数不变，随机重挑哪几手充当窗口。

    统计量 = 各层分子分母合并后的（窗口 − 对照）差。
    """
    _, den, val = METRICS[mkey]
    units2 = []
    for key, d in sorted(st.items()):
        win_ids = set(map(id, d["win"]))
        pool = [r for r in (d["win"] + d["ctl"]) if den(r) and val(r) is not None]
        widx = [j for j, r in enumerate(pool) if id(r) in win_ids]
        nw = len(widx)
        if nw == 0 or nw == len(pool):
            continue
        vals = [val(r) for r in pool]
        units2.append({"vals": vals, "nw": nw, "n": len(pool), "wsum": sum(vals[j] for j in widx)})
    if not units2:
        return None
    tn = sum(u["nw"] for u in units2)
    N = sum(u["n"] for u in units2)
    grand = sum(sum(u["vals"]) for u in units2)
    sw_obs = sum(u["wsum"] for u in units2)
    obs = sw_obs / tn - (grand - sw_obs) / (N - tn)
    rng = random.Random(seed)
    cnt = 0
    for _ in range(B):
        sw = 0.0
        for u in units2:
            vals = u["vals"]
            for j in rng.sample(range(u["n"]), u["nw"]):
                sw += vals[j]
        if abs(sw / tn - (grand - sw) / (N - tn)) >= abs(obs) - 1e-12:
            cnt += 1
    return {"obs": obs, "p": (cnt + 1) / (B + 1), "B": B, "seed": seed,
            "n_units": len(units2), "n_win": tn, "n_ctl": N - tn}


def did_cells(st):
    """2×2 差分（DiD）用的四格计数：行=窗口/对照，列=板上有无「需求」稽古格，值=取稽古。

    兜底成立 ⇒ 「有需求格时才改主」，DiD>0；只是「稽古本来就值」⇒ DiD≈0（两列同涨）。
    """
    num = [0.0] * 4
    den = [0] * 4
    for key, d in sorted(st.items()):
        for side_key, tag in (("win", 0), ("ctl", 2)):
            for r in d[side_key]:
                if not r["kb"]:
                    continue
                g = tag + (1 if r["kn_need"] else 0)
                num[g] += 1.0 if r["kind"] == "keiko" else 0.0
                den[g] += 1
    return num, den


def did_value(num, den):
    """DiD =（窗口：有需求格 − 无需求格）−（对照：有需求格 − 无需求格）。

    格子编号：0/2 = 板上**没有**需求稽古格（窗口/对照），1/3 = **有**需求稽古格。
    兜底成立 ⇒ 「只有需求格在场时才改主取稽古」⇒ DiD>0；只是「稽古本来就值」⇒ DiD≈0；
    DiD<0 ⇒ 窗口里取不取稽古跟『有没有需求格』无关甚至反向，兜底无痕迹。
    """
    if min(den) == 0:
        return None
    return (num[1] / den[1] - num[0] / den[0]) - (num[3] / den[3] - num[2] / den[2])


def perm_did(st, B, seed):
    """同上，但把每层里的「哪些手算窗口」随机重排（层内窗口手数不变）⇒ 不靠正态近似。"""
    units = []
    for key, d in sorted(st.items()):
        win_ids = set(map(id, d["win"]))
        pool = [r for r in (d["win"] + d["ctl"]) if r["kb"]]
        nw = sum(1 for r in pool if id(r) in win_ids)
        if nw == 0 or nw == len(pool):
            continue
        units.append({"v": [(1.0 if r["kind"] == "keiko" else 0.0, 1 if r["kn_need"] else 0)
                            for r in pool],
                      "w": [id(r) in win_ids for r in pool], "nw": nw})
    if not units:
        return None
    num, den = did_cells(st)
    obs = did_value(num, den)
    if obs is None:
        return {"obs": None, "num": num, "den": den, "note": "四格有空格，DiD 未定义"}
    rng = random.Random(seed)
    cnt = 0
    n_undef = 0
    for _ in range(B):
        sn = [0.0] * 4
        sd = [0] * 4
        for u in units:
            order = list(range(len(u["v"])))
            rng.shuffle(order)
            pick = frozenset(order[:u["nw"]])
            for j, (v, nf) in enumerate(u["v"]):
                g = (0 if j in pick else 2) + nf
                sn[g] += v
                sd[g] += 1
        d = did_value(sn, sd)
        if d is None:
            n_undef += 1
            continue
        if abs(d) >= abs(obs) - 1e-12:
            cnt += 1
    return {"obs": obs, "p": (cnt + 1) / (B + 1 - n_undef), "B": B - n_undef, "B_req": B,
            "seed": seed, "num": num, "den": den, "n_units": len(units), "n_undef": n_undef}


def paired(st, mkey):
    """逐 (run,场) 层的窗口−对照差，给层级/run 级 t 区间与符号检验用。"""
    _, den, val = METRICS[mkey]
    ds = []
    for key, d in sorted(st.items()):
        def rate(lst):
            v = [val(r) for r in lst if den(r) and val(r) is not None]
            return (sum(v) / len(v), len(v)) if v else (None, 0)
        a, na = rate(d["win"])
        b, nb = rate(d["ctl"])
        if a is not None and b is not None:
            ds.append({"run": key[0], "sid": key[1], "d": a - b, "nw": na, "nc": nb})
    return ds


def run_level(st, mkey):
    """同一 run 的多场合并后再算窗口−对照差（层的上一层，避免一局多场被数成多个独立样本）。"""
    _, den, val = METRICS[mkey]
    byrun = collections.defaultdict(lambda: {"win": [], "ctl": []})
    for (run, sid), d in st.items():
        byrun[run]["win"] += d["win"]
        byrun[run]["ctl"] += d["ctl"]
    ds = []
    for run, d in sorted(byrun.items()):
        def rate(lst):
            v = [val(r) for r in lst if den(r) and val(r) is not None]
            return (sum(v) / len(v), len(v)) if v else (None, 0)
        a, na = rate(d["win"])
        b, nb = rate(d["ctl"])
        if a is not None and b is not None:
            ds.append({"run": run, "sid": "all", "d": a - b, "nw": na, "nc": nb})
    return ds


def summarize(ds, label, say, pct=True):
    if not ds:
        return
    m, (lo, hi), n = ci([x["d"] for x in ds])
    pos = sum(1 for x in ds if x["d"] > 0)
    neg = sum(1 for x in ds if x["d"] < 0)
    sc, unit = (100.0, "pp") if pct else (1.0, "格")
    nz = pos + neg
    say("      " + label + " n=" + str(n) + " 均值 " + f"{sc * m:+6.2f}{unit}" + "  95%t区间 ["
        + f"{sc * lo:+6.2f},{sc * hi:+6.2f}" + "]（df=" + str(n - 1) + "，t="
        + f"{t975(n - 1):.3f}" + "）正 " + str(pos) + " 负 " + str(neg) + " 零 "
        + str(n - pos - neg) + "  精确符号检验（剔零差，有效 " + str(nz) + "）p="
        + ("—" if nz == 0 else f"{sign_p(pos, nz):.4f}"))


# ---------------------------------------------------------------- 报表

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--windows", default="3,5")
    ap.add_argument("--perm", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20261104)
    ap.add_argument("--limit", type=int, default=0,
                    help="只扫前 N 个报文（冒烟）；产物换成 *_smoke_* 后缀，不覆盖正式留档")
    args = ap.parse_args()
    if args.limit:
        g = globals()
        g.update({"CACHE": "out/_cp_sacrifice_smoke_cache.json",
                  "OUT_CSV": "out/_cp_sacrifice_smoke_hands.csv",
                  "OUT_TXT": "out/_cp_sacrifice_smoke_report.txt",
                  "SUMMARY": "out/_cp_sacrifice_smoke_summary.json"})
    ws = [int(x) for x in args.windows.split(",")]
    need = need_levels(MDB)

    out = []

    def say(s=""):
        print(s)
        out.append(s)

    def fmt(x, w=5, unit="%"):
        return (f"{100.0 * x:{w}.1f}" if x is not None else " " * (w - 1) + "—").rjust(w + 1) + unit

    if args.from_cache and os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
        say("[缓存] " + CACHE + "（未重扫报文） 生成参数 " + str(cache.get("argv")))
    else:
        cache = scan(args)
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
        say("[扫描] 报文目录重扫 ⇒ 缓存写入 " + CACHE)
    rows, cps = build_rows(cache, need)
    sc = cache["stats"]

    say("\n== 0. 覆盖与身份自检 ==")
    say("  报文文件 " + str(sc.get("files")) + " · 解码失败 " + str(sc.get("decode_fail", 0))
        + " · 动作请求 " + str(sc.get("exec_req")) + " · 训练手请求 " + str(sc.get("train_req"))
        + " · 无紧邻响应 " + str(sc.get("no_resp", 0)))
    say("  进入分析的训练手（落点 ∈ 普通/稽古格） " + str(len(rows)) + " 手 · 局 "
        + str(len({r["run"] for r in rows})) + " 局")
    say("  冻结盘面跨局串台而丢弃 " + str(sc.get("run_cross", 0)) + " 手 · 请求 turn ≠ 盘面 turn "
        + str(sc.get("turn_mismatch", 0)) + " 手（前 5 例 " + str(cache["turn_mismatch"][:5]) + "）")
    say("  result_state 分布 " + str(collections.Counter(r["state"] for r in rows)))
    byrun = collections.Counter((r["run"], bool(r["auto"])) for r in rows)
    runs = sorted({k[0] for k in byrun})
    say("  按局 自動プラン（手動/自動）：" + "  ".join(
        x.split("@")[1] + ":" + str(byrun[(x, False)]) + "/" + str(byrun[(x, True)]) for x in runs))
    say("  camp（报文 summer_camp_remain_turn_num>0）手 " + str(sum(1 for r in rows if r["camp"]))
        + " / " + str(len(rows)) + " = " + f"{100.0 * sum(1 for r in rows if r['camp']) / max(1, len(rows)):5.1f}%")

    say("\n== 0.5 映射自检：落稽古格之后，该演目 Lv 涨没涨（证 cell→act 对应，及演目 Lv 的一手增量）==")
    for c in sorted(KEIKO):
        cnt = collections.Counter()
        oth = collections.Counter()
        for r in rows:
            if r["kind"] == "keiko" and r["cell"] == c:
                cnt[r["dlv"].get(KEIKO[c], 0) if r["dlv"] else None] += 1
                for a, d in (r["dlv"] or {}).items():
                    if a != KEIKO[c]:
                        oth[d] += 1
        say("  落 " + str(c) + "（演目 " + str(KEIKO[c]) + "）n=" + str(sum(cnt.values()))
            + "  该演目 ΔLv " + str(sorted(cnt.items(), key=lambda x: (x[0] is None, x[0])))
            + "  其余演目 ΔLv " + str(sorted(oth.items())))
    pdl = collections.Counter()
    for r in rows:
        if r["kind"] == "plain":
            for d in (r["dlv"] or {}).values():
                pdl[d] += 1
    say("  普通训练手之后全部演目 ΔLv（普通格不该涨演目 Lv） " + str(sorted(pdl.items())))

    say("\n== 1. 报文里的真机场次 / 档位（mdb turn_num " + str({k: v[0] for k, v in need.items()})
        + "，need " + str({k: v[2] for k, v in need.items()}) + "）==")
    for r in sorted(cps):
        say("  " + r + "  " + "  ".join("场%s@t%s=%s" % (sid, v["turn"], TIER.get(v["tier"], v["tier"]))
                                       for sid, v in sorted(cps[r].items())))
    say("  局级场次 turn 组合 " + str(collections.Counter(
        tuple(sorted(v["turn"] for v in c.values())) for c in cps.values()))
        + " · 场次数分布 " + str(collections.Counter(len(c) for c in cps.values())))
    say("  档位构成（场×局） " + str(collections.Counter(
        v["tier"] for c in cps.values() for v in c.values())))

    st1 = stratify(rows, ws[0])
    flat = [r for d in st1.values() for s in ("win", "ctl") for r in d[s]]
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as fh:
        keys = ["run", "turn", "sid", "T", "need", "kind", "cell", "auto", "state", "camp",
                "lvs", "low", "nlow", "kb", "needcells", "kn_need", "o1_val", "o2_val",
                "m1c_den", "m1c_val", "m3c_den", "m3c_val", "m5_den", "m5_val", "m2_den",
                "m2_val", "m3_base", "m4_den", "m4_val", "board", "file"]
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in sorted(flat, key=lambda x: (x["run"], x["turn"])):
            w.writerow({k: (json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], (list, dict))
                            else r.get(k)) for k in keys})
    say("\n  逐手明细（W=" + str(ws[0]) + " 口径，剔 camp）→ " + OUT_CSV + " " + str(len(flat)) + " 行")

    say("\n== 2. 主判据：窗口 vs 同局同场对照（两档 W 并列；剔 camp）==")
    say("   供给（系统给不给，不是玩家选择）O1=板上有稽古格 · O2=板上有『需求』稽古格")
    say("   选择（玩家动的手）M1=稽古手占比 · M1c=稽古格在场时取稽古 · M3c=需求格在场时取需求格 · "
        "M5=有稽古格但无需求格时取稽古（M3c 的差分对照）")
    say("   描述 M2=每手（含普通手）落需求格的比例 · M4=落点缺口−板上稽古格平均缺口（只算板上≥2 稽古格的手，负=挑更缺的）")
    summary = {}
    for W in ws:
        st = stratify(rows, W)
        pw = [r for d in st.values() for r in d["win"]]
        pc = [r for d in st.values() for r in d["ctl"]]
        say("  W=" + str(W) + "  窗口手 " + str(len(pw)) + " / 对照手 " + str(len(pc))
            + "  层数 " + str(len(st)))
        for mk in ("O1", "O2", "M1", "M1c", "M3c", "M5", "M2"):
            a, b = stat(pw, mk), stat(pc, mk)
            if a and b:
                say("    " + mk.ljust(3) + " 窗口 " + str(a["n"]).rjust(4) + " 手 " + fmt(a["rate"])
                    + "   对照 " + str(b["n"]).rjust(4) + " 手 " + fmt(b["rate"])
                    + "   差 " + f"{100.0 * (a['rate'] - b['rate']):+6.2f}pp")
        a, b = stat(pw, "M4"), stat(pc, "M4")
        if a and b:
            say("    M4  窗口 " + str(a["n"]).rjust(4) + " 手 " + f"{a['rate']:+7.3f}格"
                + "   对照 " + str(b["n"]).rjust(4) + " 手 " + f"{b['rate']:+7.3f}格"
                + "   差 " + f"{a['rate'] - b['rate']:+7.3f}格")
        num, den = did_cells(st)
        dv = did_value(num, den)
        cell = lambda i: (f"{100.0 * num[i] / den[i]:5.1f}" if den[i] else "    —  ")  # noqa: E731
        say("    DiD 四格（取稽古率 | 有无需求稽古格 × 窗口/对照）："
            + " 窗口·有需求 " + cell(1) + "%/" + str(den[1])
            + "  窗口·无需求 " + cell(0) + "%/" + str(den[0])
            + "  对照·有需求 " + cell(3) + "%/" + str(den[3])
            + "  对照·无需求 " + cell(2) + "%/" + str(den[2])
            + "  ⇒ DiD " + (f"{100.0 * dv:+6.2f}pp" if dv is not None else "   未定义")
            + "（>0＝只在有需求格时才改主＝兜底方向；≈0＝稽古本来就值；<0＝兜底无痕迹）")
        for mk in ("O2", "M1c", "M3c", "M5"):
            pr = perm_test(st, mk, args.perm, args.seed)
            if pr:
                say("      " + mk + " 置换（层内 turn 标签，B=" + str(pr["B"]) + " seed="
                    + str(pr["seed"]) + "，有效层 " + str(pr["n_units"]) + "，窗口手 "
                    + str(pr["n_win"]) + "/对照手 " + str(pr["n_ctl"]) + "）合并差 "
                    + f"{100.0 * pr['obs']:+6.2f}pp  双侧 p=" + f"{pr['p']:.4f}")
        pd_ = perm_did(st, args.perm, args.seed)
        if pd_ and pd_.get("obs") is not None:
            say("      DiD 置换（同上 B=" + str(str(pd_["B"])) + "，跳过空格 " + str(pd_["n_undef"])
                + " 次）观测 " + f"{100.0 * pd_['obs']:+6.2f}pp  双侧 p=" + f"{pd_['p']:.4f}")
        for mk in ("O1", "O2", "M1", "M1c", "M3c", "M5", "M2"):
            summarize(paired(st, mk), mk + " 层配对差", say)
        summarize(paired(st, "M4"), "M4 层配对差", say, pct=False)
        for mk in ("M1c", "M3c", "M5"):
            summarize(run_level(st, mk), mk + " run 级（同局多场合并）", say)
        summary[str(W)] = {"n_win": len(pw), "n_ctl": len(pc),
                           "stat_win": {mk: stat(pw, mk) for mk in METRICS},
                           "stat_ctl": {mk: stat(pc, mk) for mk in METRICS},
                           "did": {"num": num, "den": den, "value": dv, "perm": pd_},
                           "paired": {mk: paired(st, mk) for mk in METRICS},
                           "run_level": {mk: run_level(st, mk) for mk in ("M1c", "M3c", "M5")},
                           "perm": {mk: perm_test(st, mk, args.perm, args.seed)
                                    for mk in ("O2", "M1c", "M3c", "M5")}}

    say("\n== 3. 距离曲线（与窗口宽度无关的形状读数）：dt = 该场 turn − 本手 turn ==")
    say("   剔 camp；O1/O2 是系统供给，M1c 是「稽古在场取不取」，M3c 是「需求格在场取不取」，"
        "nlow = 该手选择时未达标演目数")
    for dt in range(1, MAX_DT + 1):
        lst = [r for r in rows if not r["camp"] and r["T"] is not None and r["T"] - r["turn"] == dt]
        if not lst:
            continue
        s1, s3, o1, o2 = stat(lst, "M1c"), stat(lst, "M3c"), stat(lst, "O1"), stat(lst, "O2")
        pct = lambda s: (f"{100.0 * s['rate']:5.1f}" if s else "    —  ")  # noqa: E731
        say("   dt=" + str(dt).rjust(2) + "  n=" + str(len(lst)).rjust(3)
            + "  稽古格在场 " + pct(o1) + "%  需求格在场 " + pct(o2) + "%"
            + "  M1c 取稽古 " + pct(s1) + "%（分母 " + (str(s1["n"]) if s1 else "0") + "）"
            + "  M3c 取需求格 " + pct(s3) + "%（分母 " + (str(s3["n"]) if s3 else "0") + "）"
            + "  nlow 均值 " + f"{sum(r['nlow'] for r in lst) / len(lst):4.2f}"
            + "  板上稽古格均值 " + f"{sum(len(r['kb']) for r in lst) / len(lst):4.2f}")

    say("\n== 4. 分场次（O2 / M1c / M3c 三列，窗口 vs 对照）==")
    for W in ws:
        st_all = stratify(rows, W)
        for sid in sorted({k[1] for k in st_all}):
            st = {k: v for k, v in st_all.items() if k[1] == sid}
            pw = [r for d in st.values() for r in d["win"]]
            pc = [r for d in st.values() for r in d["ctl"]]
            cells = []
            for mk in ("O2", "M1c", "M3c"):
                a, b = stat(pw, mk), stat(pc, mk)
                cells.append(mk + " " + (f"{100.0 * a['rate']:5.1f}" if a else "    — ") + "%/"
                             + (str(a["n"]) if a else "0") + " vs "
                             + (f"{100.0 * b['rate']:5.1f}" if b else "    — ") + "%/"
                             + (str(b["n"]) if b else "0"))
            say("  W=" + str(W) + " 场" + str(sid) + "  " + "   ".join(cells))

    say("\n== 5. 口径敏感性（同一 W，只换口径；不换窗口）==")
    for tag, kw in (("主口径（剔 camp）", {}),
                    ("只 camp 内", {"camp": "only"}),
                    ("camp 不剔", {"camp": "keep"}),
                    ("只手動（排除自動プラン）", {"manual": True}),
                    ("只自動プラン", {"manual": False})):
        for W in ws:
            st = stratify(rows, W, **kw)
            pw = [r for d in st.values() for r in d["win"]]
            pc = [r for d in st.values() for r in d["ctl"]]
            a3, b3 = stat(pw, "M3c"), stat(pc, "M3c")
            a1, b1 = stat(pw, "M1c"), stat(pc, "M1c")
            say("  " + tag.ljust(24) + " W=" + str(W) + "  窗口 M3c "
                + str(a3["n"] if a3 else 0).rjust(3) + " 手 "
                + (f"{100.0 * a3['rate']:5.1f}" if a3 else "   —  ") + "%   对照 "
                + str(b3["n"] if b3 else 0).rjust(3) + " 手 "
                + (f"{100.0 * b3['rate']:5.1f}" if b3 else "   —  ") + "%   差 "
                + (f"{100.0 * (a3['rate'] - b3['rate']):+6.1f}pp" if a3 and b3 else "   —  ")
                + "   ‖ M1c 差 "
                + (f"{100.0 * (a1['rate'] - b1['rate']):+6.1f}pp" if a1 and b1 else "  —  "))

    say("\n== 6. 主动改打：连续两手的变化类型，及与该场最终档位的并列 ==")
    say("   区分三类：『普通→稽古』（玩家改主）·『稽古↔另一稽古格』（多半是上板轮换，不是选择）·"
        "『没取需求格→取需求格』（兜底的可见痕迹）")
    seq = collections.defaultdict(list)
    for r in rows:
        seq[r["run"]].append(r)
    for lst in seq.values():
        lst.sort(key=lambda r: r["turn"])
    cnt = collections.defaultdict(collections.Counter)
    for run, lst in sorted(seq.items()):
        for prev, cur in zip(lst, lst[1:]):
            if cur["T"] is None or cur["turn"] - prev["turn"] > PAIR_GAP:
                continue
            if cur["camp"] or prev["camp"] or prev["sid"] != cur["sid"]:
                continue
            tier = (cps.get(run, {}) or {}).get(cur["sid"], {}).get("tier")
            to_k = 1 if (cur["kind"] == "keiko" and prev["kind"] == "plain" and cur["kb"]) else 0
            kk_chg = 1 if (cur["kind"] == "keiko" and prev["kind"] == "keiko"
                           and cur["cell"] != prev["cell"]) else 0
            to_need = 1 if (cur["kn_need"] and cur["cell"] in cur["needcells"]
                            and not (prev["kn_need"] and prev["cell"] in prev["needcells"])) else 0
            drop_need = 1 if (prev["kn_need"] and prev["cell"] in prev["needcells"]
                              and cur["kn_need"] and cur["cell"] not in cur["needcells"]) else 0
            for W in ws:
                sd = side(cur, W)
                if sd is None:
                    continue
                tags = [(W, sd, "all"), (W, "tier%s" % tier, "all")] if sd == "win" \
                    else [(W, sd, "all")]
                for tg in tags:
                    c = cnt[tg]
                    c["n"] += 1
                    c["to_keiko"] += to_k
                    c["keiko_chg"] += kk_chg
                    c["to_need"] += to_need
                    c["drop_need"] += drop_need
                    c["plain_prev"] += 1 if prev["kind"] == "plain" else 0
                    c["keiko_cur"] += 1 if cur["kind"] == "keiko" else 0
    for W in ws:
        a, b = cnt[(W, "win", "all")], cnt[(W, "ctl", "all")]
        if a["n"] and b["n"]:
            say("  W=" + str(W) + "  窗口连续对 " + str(a["n"]).rjust(4) + "  改主取稽古 "
                + f"{100.0 * a['to_keiko'] / a['n']:5.1f}%" + "（上一手是普通的对 "
                + str(a["plain_prev"]) + "）  稽古换稽古格 " + f"{100.0 * a['keiko_chg'] / a['n']:5.1f}%"
                + "  换向需求格 " + f"{100.0 * a['to_need'] / a['n']:5.1f}%"
                + "  丢需求格 " + f"{100.0 * a['drop_need'] / a['n']:5.1f}%"
                + "   ‖  对照 " + str(b["n"]).rjust(4) + " 对 改主取稽古 "
                + f"{100.0 * b['to_keiko'] / b['n']:5.1f}%" + "（上一手普通 " + str(b["plain_prev"])
                + "）  稽古换格 " + f"{100.0 * b['keiko_chg'] / b['n']:5.1f}%"
                + "  换向需求格 " + f"{100.0 * b['to_need'] / b['n']:5.1f}%"
                + "  丢需求格 " + f"{100.0 * b['drop_need'] / b['n']:5.1f}%")
        for tier in (2, 1, None):
            c = cnt[(W, "tier%s" % tier, "all")]
            if c["n"]:
                say("      窗口内 · 该场最终 " + TIER.get(tier, "未结算/无档位记录") + " 连续对 "
                    + str(c["n"]).rjust(4) + "  改主取稽古 "
                    + f"{100.0 * c['to_keiko'] / c['n']:5.1f}%  换向需求格 "
                    + f"{100.0 * c['to_need'] / c['n']:5.1f}%  丢需求格 "
                    + f"{100.0 * c['drop_need'] / c['n']:5.1f}%")

    with open(OUT_TXT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    json.dump(summary, open(SUMMARY, "w", encoding="utf-8"), ensure_ascii=False, indent=1,
              default=str)
    print("\n[写出] " + OUT_CSV + " / " + OUT_TXT + " / " + SUMMARY)


if __name__ == "__main__":
    main()
