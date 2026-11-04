#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务 #57：把「运气档」做成报文里能数出来的可观测量。

口径与红线
----------
- 一手来源 = `E:/HJM/jidianbeiAI0.22/out/hachimi_raw`（**只读**：不移动、不改名、不删除）。
- 解码器 = `E:/HJM/jidianbeiAI0.22/msgpack_lite.py` 的 `decode_file`（没有 `unpackb`）。
- 文件时序 = (`mtime`, 文件名)，直接复用 `tools/pt_ledger_scan.py` 的 `sort_key`
  （报文名里的 `HHMMSS` 跨天会乱序，只有 mtime 是捕获顺序）。
- 定局 = `chara_info.single_mode_chara_id` + `start_time`；**不带 `chara_info` 的响应**
  （终局结算、部分比赛/事件包）按时间序归到**上一条 `chara_info` 所在的局**（同 `pt_ledger_scan.py`）。
- 训练手的配对**沿用 AGENTS.md 定案与 `tools/hand_gain.py` 的口径**：
  P = 该回合最后一条带预告（`command_info_array` 里**有非空 `params_inc_dec_info_array`**）的报文；
  R = P 之后**同回合**、且**不是 P 本身**的第一条带 `command_result` 的报文；
  Δ 用 R 的终局五维减 P 的五维（跨回合相减是我第一版踩过的坑，已修）。
  与 hand_gain 的三处已知口径差，全部在 Q1b 里逐手对账（拿 `out/_hand_gain.json` 的留档做基线）：
  ① 时序本文用 (`mtime`, 文件名)，hand_gain 用文件名字典序（跨天会乱序）；
  ② 结算包自身也带预告时 hand_gain 直接不作 R（该回合漏配），本文允许它配**更早**那条 P
     ⇒ 手数可能略多，`stats.hand_r_also_board` 报这种手有几笔；
  ③ 本文对 `chara_info` 只要求有 `turn`，hand_gain 还要求 `max_*` 五键齐全。
- 回合轴：报文 turn ＝ 面板 turn ＝ 表行号（1 基），引擎 `base.turn` ＝ 报文 − 1；
  逐回合的表**同时**给两轴，不出现裸 turn。
- **不写 `data/`、不引入拟合常数、不当乘区。** 只做计数与非参数关联强度。

复现
----
    python tools/luck_census.py                 # 全扫报文（约 150 s），写缓存
    python tools/luck_census.py --from-cache    # 只读缓存重建全部读数
"""

from __future__ import annotations

import argparse
import collections
import csv
import glob
import itertools
import json
import math
import os
import random
import re
import statistics as st
import sys
import time

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402  复用 get_ci / sort_key / CHANNELS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = PS.RAW + "/"
CONST = os.path.join(REPO, "data", "constants.json")
CACHE = "out/_luck_pkt_cache_20261105.json"
OUT_TXT = "out/_luck_census_20261105.txt"
OUT_CSV = "out/_luck_census_20261105.csv"
OUT_JSON = "out/_luck_summary_20261105.json"

PK = ["speed", "stamina", "power", "guts", "wiz"]
MK = ["max_" + k for k in PK]
DM = ["default_max_" + k for k in PK]
TT = {10: "vital", 30: "sp"}

# ---------------------------------------------------------------- 分层判据
# 全部在跑之前写死；跑完一条不改，事后补的标「事后」。
#
# D1 回合分层用**合宿开始**的面板落点 13 / 37 / 61（一手 = `docs/合宿开始与教诲改派轴_20261104.md`：
#    合宿开始增益落引擎 12/36/60）⇒ 切 4 段，末段（面板 61–78＝引擎 60–77）单独报。
# D2 训练随机档 = `command_result.result_state`。判据不靠猜：配对口径下 state=1 的手
#    Δ五维和**恒为 0**、且其预告 `failure_rate` 明显高，state=2 的手 Δ五维和为正
#    （Q1 逐档报 n、区间、负数计数）。若全库只有 {1,2} 两档 ⇒「训练大成功」在报文侧**不可识别**。
# D3 発表会档 = `festa_data_set.check_point_result_array[].result_type`，1=成功 / 2=大成功
#    （`docs/发表会档位一手_20261103.md` #16 已定案，本文沿用，不重新判方向）。
# D4 事件「条数」认**去重后的 `event_id`**（同一事件的每一格对白都会重发一条 check_event 请求，
#    所以请求条数 = 点击数，不是事件数）；首次播放回合取该 event_id 第一条请求的 `current_turn`。
#    `unchecked_event_array` 是待播队列，只用于分类与小游戏/继承事件标记。
# D5 固定 vs 随机：同一 `event_id` 在整局样本多局里的**首次播放回合不完全相同** ⇒ 判随机；
#    逐局一致 ⇒ 剧本固定项。只用这一条可复核判据（本任务不混表侧证据）。
# D6 比赛名次基线 = 同一条 `race_start_info.race_horse_data[]` 里**所有参赛马的五维和**：
#    预测名次 = 1 + #(五维和严格高于本马)。按 `program_id` 取**首见**快照；同场再见到不同基线
#    只打 `ambig` 标记（不取均值、不改值）。报文自带的 `popularity`（人气序）作次要基线/描述。
# D7 运气代理**预先写死 3 个**，等权 z 分合成，不折算成分数：
#    L1 失败偏离 = 实际失败手数 − 期望失败手数（期望 = Σ 配对口径下所选手的 `failure_rate`/100）
#    L2 発表会大成功场数（D3）
#    L3 名次偏离 = Σ(预测名次 − 实际名次)，正 = 运气好
#    L4（技能发动次数 / 提示新增 / 事件条数 / 随机事件数 / dash 类计数）**只作描述**：
#        它们同时吃练度与决策，是混合项，不进气球合成。
# D8 n ≤ 13 局 ⇒ 只报形状与相关强度（Spearman ρ + 双尾置换 p：n≤8 穷举，否则 2 万次、种子 20261105），
#    并报该设计的 p 下限 2/n!；不写斜率、不写乘区、不写 `data/`；关联一律标「相关，非机制」。
# D9 终分分量：五维分（终局五维 → 仓里 `data/constants.json` 的 `five_status_final_score`
#    + `revise(v)= v if v≤1200 else 2v−1200`）；SP 毛增两种口径分开报 ——
#    `SP手` = 配对训练手内部的正 Δ`skill_point` 合计（不含事件/比赛来源），
#    `SP回合` = 相邻回合末快照的正 Δ 合计（会被技能花费压低，只作对照，别当毛增）；
#    官方分 = `.single_mode_finish_common.trained_chara[].rank_score`，
#    连接主键 = 该局逐回合 `chara_info.succession_trained_chara_id_1/_2` 等于该记录的
#    同名字段（#54 定案）；候选数 ≠ 1 ⇒ 只允许再用一把**预先声明的副键**（继承对相同 **且**
#    终局五维逐轴相同）收口，仍不唯一 ⇒ 报「没读到」，不猜。
#    【事后修订 11-05，原因不是读数而是归局】终局结算包**不含 `chara_info`**，且常在
#    「下一局已开局」之后才到 ⇒ 按时间序归局会把上一局的记录挂到下一局名下
#    （实测：run171 的记录落在 172 的桶里，171 报候选 0）。故记录改放**全局池**，
#    认局只靠上面那两把键；这不改变判据，只修归属。

SEG = [("前期", 1, 12), ("一段合宿后", 13, 36), ("二段合宿后", 37, 60), ("末段", 61, 78)]


def revise(v):
    return v if v <= 1200 else 2 * v - 1200


def stat_score(tbl, five):
    last = len(tbl) - 1
    return sum(tbl[min(int(revise(v or 0)), last)] for v in five)


def ev_key(e):
    ci = e.get("event_contents_info") or {}
    return "%s|%s|%s|%s" % (e.get("event_id"), e.get("story_id"),
                            e.get("chara_id") or 0, ci.get("support_card_id") or 0)


def _walk(obj, prefix, depth, seen):
    if depth < 0:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = "%s.%s" % (prefix, k) if prefix else k
            seen.add(p)
            _walk(v, p, depth - 1, seen)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, prefix + "[]", depth - 1, seen)


def note_paths(data, out, depth=5):
    """把一条报文里出现过的**字段路径**（数组用 [] 表示）记进计数器，每条路径每包最多 +1。"""
    seen = set()
    _walk(data, "", depth, seen)
    for p in seen:
        out[p] += 1


def board_of(data, ks=None):
    """预告板：command_id(str) -> 失败率 / 演目Lv / 参加人数 / 提示人数 / 预告五维和 / 预告SP。"""
    out = {}
    for holder in ("home_info", "festa_data_set"):
        for c in (data.get(holder) or {}).get("command_info_array") or []:
            if ks is not None:
                ks["board:" + holder] |= set(c.keys())
            cid = c.get("command_id")
            pa = c.get("params_inc_dec_info_array") or []
            if cid is None or not pa:
                continue          # 与 hand_gain 的 preds_of 同：没有预告数组的板骨架不算预告
            s = sp = 0
            for e in pa:
                tt = TT.get(e.get("target_type"), e.get("target_type"))
                v = e.get("value") or 0
                if tt == "sp":
                    sp += v
                elif isinstance(tt, int):
                    s += v
            d = out.setdefault(str(cid), {"fr": 0, "lv": 0, "pt": 0, "tip": 0,
                                          "pred": 0, "pred_sp": 0, "upp": 0})
            d["fr"] = max(d["fr"], c.get("failure_rate") or 0)
            d["lv"] = max(d["lv"], c.get("level") or 0)
            d["upp"] = max(d["upp"], 1 if c.get("is_upper_training_event_occurred") else 0)
            d["pt"] += len(c.get("training_partner_array") or [])
            d["tip"] += len(c.get("tips_event_partner_array") or [])
            d["pred"] += s
            d["pred_sp"] += sp
    return out


def req_read(ch, common):
    return {"ch": ch, "turn": common.get("current_turn"),
            "event_id": common.get("event_id", ""),
            "event_chara": common.get("chara_id", ""),
            "choice": common.get("choice_number", ""),
            "auto": common.get("exec_auto_play_plan_id", ""),
            "ctype": common.get("command_type", ""),
            "cid": common.get("command_id", ""),
            "select_id": common.get("select_id", "")}


def new_run(chara, start, ci):
    return {"chara": chara, "start": start, "scen": ci.get("scenario_id"),
            "card_id": ci.get("card_id"), "succ": [ci.get("succession_trained_chara_id_1"),
                                                   ci.get("succession_trained_chara_id_2")],
            "snaps": {}, "reqs": [], "races_pre": {}, "races_res": {}, "cps": {},
            "evq": {}, "hist": {}, "hands": [],
            "cp_len_max": 0, "no_ci_resp": 0, "upp_resp": 0}


def scan(limit=0):
    files = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=PS.sort_key)
    if limit:
        files = files[:limit]
    stats = collections.Counter()
    KS = collections.defaultdict(set)            # 字段清单（Q8 判「读不到」的证据）
    KPATH = collections.Counter()                # 结算包子树里的字段路径
    POOL = {}                                    # 终局记录**全局池**（不按时间序归局，见 D9 修订）
    runs = {}
    cur = None                                  # 当前局键（按时间序延续）
    P = None                                    # 配对手：本回合最后一条带预告的报文
    for path in files:
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
            if hit is None:
                continue
            ch = PS.CHANNELS[hit]
            rec = req_read(ch, d[hit] or {})
            rec["file"] = name
            stats["req:" + ch] += 1
            if cur is not None:
                runs[cur]["reqs"].append(rec)
            else:
                stats["req_orphan"] += 1
            continue
        if not name.endswith("_notify_response.bin"):
            continue
        data = d.get("data") or {}
        if not isinstance(data, dict):
            continue
        ci = PS.get_ci(data)
        has_ci = isinstance(ci, dict) and ci.get("turn") is not None
        if not has_ci:
            stats["resp_no_ci"] += 1
        bd = board_of(data, KS)
        cr = data.get("command_result") or {}
        cr = cr if isinstance(cr, dict) and cr.get("command_id") is not None else None
        if cr is not None:
            KS["command_result"] |= set(cr.keys())
            stats["cr_seen"] += 1
            stats["cr_state:%s" % cr.get("result_state")] += 1
            note_paths(data, KPATH, 5)           # 结算包整棵子树的字段清单（Q8 用）
        # 终局记录进**全局池**：结算包常在「下一局已开局」之后才到，按时间序归局会错档
        # （11-05 实测：run171 的记录落在 172 名下）⇒ 认局改靠继承对主键 + 终局五维副键，见 D9。
        fin = data.get("single_mode_finish_common")
        if isinstance(fin, dict):
            KS["single_mode_finish_common"] |= set(fin.keys())
            stats["fin_pkt"] += 1
            for tc in fin.get("trained_chara") or []:
                if not isinstance(tc, dict):
                    continue
                KS["single_mode_finish_common.trained_chara[]"] |= set(tc.keys())
                POOL[str(tc.get("trained_chara_id"))] = {
                    "tc": tc.get("trained_chara_id"), "rs": tc.get("rank_score"),
                    "five": [tc.get(k) or 0 for k in PK],
                    "succ": [tc.get("succession_trained_chara_id_1"),
                             tc.get("succession_trained_chara_id_2")],
                    "card": tc.get("card_id"), "sp_total": tc.get("total_skill_pt")}
        if has_ci:
            KS["chara_info"] |= set(ci.keys())
            chara = ci.get("single_mode_chara_id")
            start = ci.get("start_time")
            rk = "%s@%s" % (chara, start)
            r = runs.get(rk)
            if r is None:
                r = runs[rk] = new_run(chara, start, ci)
            cur = rk
            t = ci.get("turn")
            snap = r["snaps"].setdefault(str(t), {"t": t})
            snap.update({"five": [ci.get(k) or 0 for k in PK],
                         "cap": [ci.get(k) or 0 for k in MK],
                         "dmax": [ci.get(k) or 0 for k in DM],
                         "sp": ci.get("skill_point") or 0, "vital": ci.get("vital"),
                         "mot": ci.get("motivation"), "fans": ci.get("fans"),
                         "nsk": len(ci.get("skill_array") or []),
                         "ntip": len(ci.get("skill_tips_array") or []),
                         "nnick": len(ci.get("nickname_id_array") or []),
                         "eff": sorted(set(ci.get("chara_effect_id_array") or [])),
                         "style": ci.get("race_running_style"),
                         "file_last": name})
            stats["resp_with_ci"] += 1
            # ---- 配对（先判 R，再把本包立为新的 P；同一条包不能既 P 又 R）
            if cr is not None and P is not None and P["t"] == t and P["file"] != name:
                e = P["board"].get(str(cr.get("command_id")))
                if e is not None:
                    d5 = [snap["five"][k] - P["five"][k] for k in range(5)]
                    r["hands"].append({"t_panel": int(t), "t_eng": int(t) - 1,
                                       "cid": str(cr.get("command_id")), "sub": cr.get("sub_id"),
                                       "state": cr.get("result_state"),
                                       "fr": e["fr"], "lv": e["lv"], "pt": e["pt"],
                                       "tip": e["tip"], "pred": e["pred"], "pred_sp": e["pred_sp"],
                                       "upp": e.get("upp", 0),
                                       "d5": sum(d5), "d_sp": snap["sp"] - P["sp"],
                                       "d_vital": (snap["vital"] or 0) - (P["vital"] or 0),
                                       "d_mot": (snap["mot"] or 0) - (P["mot"] or 0),
                                       "r_board": bool(bd),
                                       "file_P": P["file"], "file_R": name})
                    stats["hand_paired"] += 1
                    if bd:
                        stats["hand_r_also_board"] += 1
            if bd:
                P = {"t": t, "board": bd, "five": list(snap["five"]), "sp": snap["sp"],
                     "vital": snap["vital"], "mot": snap["mot"], "file": name}
        else:
            r = runs.get(cur) if cur is not None else None
            if r is not None:
                r["no_ci_resp"] += 1
        if r is None:
            continue
        if any(v.get("upp") for v in bd.values()):
            r["upp_resp"] += 1
        fd = data.get("festa_data_set") or {}
        if isinstance(fd, dict):
            KS["festa_data_set"] |= set(fd.keys())
        ca = fd.get("check_point_result_array") or []
        if isinstance(ca, list):
            r["cp_len_max"] = max(r["cp_len_max"], len(ca))
            for e in ca:
                if isinstance(e, dict):
                    KS["festa_data_set.check_point_result_array[]"] |= set(e.keys())
                r["cps"][str(e.get("schedule_id"))] = {"schedule_id": e.get("schedule_id"),
                                                       "rt": e.get("result_type")}
        for e in (data.get("unchecked_event_array") or []):
            if isinstance(e, dict):
                KS["unchecked_event_array[]"] |= set(e.keys())
            q = r["evq"].setdefault(ev_key(e), {"first_t": None, "seen": 0, "choice": 0,
                                                "mini": 0, "succ": 0,
                                                "play_timing": e.get("play_timing")})
            q["seen"] += 1
            if has_ci and q["first_t"] is None:
                q["first_t"] = ci.get("turn")
            if (e.get("event_contents_info") or {}).get("choice_array"):
                q["choice"] = 1
            if e.get("minigame_result"):
                q["mini"] = 1
            if e.get("succession_event_info"):
                q["succ"] = 1
        if data.get("event_effected_factor_array"):
            stats["resp_with_eff_fac"] += 1
        rsi = data.get("race_start_info")
        if isinstance(rsi, dict):
            KS["race_start_info"] |= set(rsi.keys())
            ent = rsi.get("race_horse_data") or []
            for h in ent:
                if isinstance(h, dict):
                    KS["race_start_info.race_horse_data[]"] |= set(h.keys())
                    for x in (h.get("race_result_array") or []):
                        if isinstance(x, dict):
                            KS["race_start_info.race_horse_data[].race_result_array[]"] |= set(x.keys())
            ours = next((h for h in ent if h.get("viewer_id") and
                         h.get("single_mode_chara_id") == r["chara"]), None)
            prog = str(rsi.get("program_id"))
            if ours is not None:
                osum = sum(ours.get(k) or 0 for k in PK)
                pred = 1 + sum(1 for h in ent if h is not ours and
                               sum(h.get(k) or 0 for k in PK) > osum)
                rec = {"prog": rsi.get("program_id"),
                       "t": ci.get("turn") if has_ci else None, "seed": rsi.get("random_seed"),
                       "season": rsi.get("season"), "weather": rsi.get("weather"),
                       "sub_weather": rsi.get("sub_weather"),
                       "ground": rsi.get("ground_condition"), "n": len(ent),
                       "ours": osum, "pred": pred, "pop": ours.get("popularity"),
                       "frame": ours.get("frame_order"), "style": ours.get("running_style"),
                       "n_skill": len(ours.get("skill_array") or []), "n_snap": 1}
                old = r["races_pre"].get(prog)
                if old is None:
                    r["races_pre"][prog] = rec
                else:
                    old["n_snap"] += 1
                    if old["pred"] != pred or old["ours"] != osum:
                        old["ambig"] = 1
                for e in ours.get("race_result_array") or []:
                    ek = "%s_%s" % (e.get("turn"), e.get("program_id"))
                    r["races_res"].setdefault(ek, {
                        "t": e.get("turn"), "prog": str(e.get("program_id")),
                        "race_num": e.get("race_num"), "rank": e.get("result_rank"),
                        "skill_act": e.get("skill_activate_count"),
                        "dash": e.get("start_dash_state"), "frame": e.get("frame_order"),
                        "pop": e.get("popularity"), "weather": e.get("weather"),
                        "ground": e.get("ground_condition"), "mot": e.get("motivation"),
                        "diff": e.get("bashin_diff"), "diff_top": e.get("bashin_diff_from_top"),
                        "lsr": e.get("last_straight_line_rank"), "time": e.get("result_time")})
        for e in (data.get("race_history") or []):
            r["hist"].setdefault("%s_%s" % (e.get("turn"), e.get("program_id")), e)
    return {"stats": dict(stats), "runs": runs, "finish_pool": POOL,
            "keysets": {k: sorted(v) for k, v in KS.items()},
            "settle_paths": dict(KPATH)}


# ---------------------------------------------------------------- 逐局读数

def run_row(rk, r, tbl, pool):
    snaps = sorted(r["snaps"].values(), key=lambda s: int(s["t"]))
    hs = r["hands"]
    ts = [int(s["t"]) for s in snaps]
    fail = [h for h in hs if h["state"] == 1]
    ok = [h for h in hs if h["state"] == 2]
    exp_fr = sum(h["fr"] for h in hs) / 100.0
    sp_hand = sum(max(0, h["d_sp"]) for h in hs)
    sp_turn = sum(max(0, snaps[i]["sp"] - snaps[i - 1]["sp"]) for i in range(1, len(snaps)))
    tip_gain = sum(max(0, snaps[i]["ntip"] - snaps[i - 1]["ntip"]) for i in range(1, len(snaps)))
    sk_gain = sum(max(0, snaps[i]["nsk"] - snaps[i - 1]["nsk"]) for i in range(1, len(snaps)))
    lo, hi = (min(ts), max(ts)) if ts else (None, None)
    ev_req = [q for q in r["reqs"] if q["ch"] == "event"]
    ev_in = [q for q in ev_req if q.get("turn") is not None and lo is not None
             and lo <= q["turn"] <= hi]
    ev_first = {}
    for q in ev_in:
        ev_first.setdefault(str(q["event_id"]), q["turn"])
    auto_vals = dict(collections.Counter(str(q.get("auto")) for q in ev_in))
    choice_vals = dict(collections.Counter(str(q.get("choice")) for q in ev_in))
    eff_seen, eff_new = set(), 0
    for sn in snaps:
        add = [e for e in (sn.get("eff") or []) if e not in eff_seen]
        eff_new += len(add)
        eff_seen.update(add)
    races = []
    for ek, res in sorted(r["races_res"].items(), key=lambda kv: (kv[1].get("t") or 0)):
        pre = r["races_pre"].get(str(res["prog"]))
        races.append({**res, "pred": None if not pre else pre["pred"],
                      "ambig": 1 if (pre or {}).get("ambig") else 0,
                      "n_ent": None if not pre else pre["n"],
                      "ours": None if not pre else pre["ours"],
                      "seed": None if not pre else pre["seed"],
                      "pre_snaps": None if not pre else pre["n_snap"]})
    dev = [(x["pred"] - x["rank"]) for x in races
           if x.get("pred") and x.get("rank") and not x["ambig"]]
    devp = [(x["pop"] - x["rank"]) for x in races if x.get("pop") and x.get("rank")]
    cps = sorted(r["cps"].values(), key=lambda x: int(x["schedule_id"] or 0))
    last = snaps[-1] if snaps else None
    succ = r.get("succ")
    cand = [f for f in pool.values() if f.get("succ") == succ] if succ else []
    five_match = [f for f in cand if last and f.get("five") == last.get("five")]
    rank_score = cand[0]["rs"] if len(cand) == 1 else (
        five_match[0]["rs"] if len(five_match) == 1 else None)
    return {
        "run": rk, "chara": r["chara"], "start": r["start"], "scen": r.get("scen"),
        "succ": r.get("succ"),
        "tp_min": lo, "tp_max": hi, "te_min": (lo - 1) if lo is not None else None,
        "te_max": (hi - 1) if hi is not None else None, "n_snap": len(snaps),
        "no_ci_resp": r["no_ci_resp"],
        "five_end": last["five"] if last else None,
        "five_sum_end": sum(last["five"]) if last else None,
        "cap_sum_end": sum(last["cap"]) if last else None,
        "dmax_all_1200": bool(last) and all(v == 1200 for v in last["dmax"]),
        "score_end": stat_score(tbl, last["five"]) if last else None,
        "sp_hand": sp_hand, "sp_turn": sp_turn,
        "rank_score": rank_score,
        "rank_cand": len(cand), "rank_five_match": len(five_match),
        "rank_join": ("主键唯一" if len(cand) == 1 else
                      ("副键唯一" if len(five_match) == 1 else None)),
        "pool_n": len(pool),
        "n_hand": len(hs), "n_ok": len(ok), "n_fail": len(fail),
        "n_other_state": len([h for h in hs if h["state"] not in (1, 2)]),
        "exp_fail": round(exp_fr, 3), "L1": round(len(fail) - exp_fr, 3),
        "state_values": sorted({h["state"] for h in hs if h["state"] is not None}),
        "n_upp_resp": r.get("upp_resp", 0),
        "n_upp_hand": sum(1 for h in hs if h.get("upp")),
        "fail_by_seg": {nm: [sum(1 for h in fail if a <= h["t_panel"] <= b),
                             round(sum(h["fr"] for h in hs if a <= h["t_panel"] <= b) / 100.0, 3),
                             sum(1 for h in hs if a <= h["t_panel"] <= b)]
                        for nm, a, b in SEG},
        "n_cp": len(cps), "n_great": sum(1 for c in cps if c["rt"] == 2),
        "n_success_cp": sum(1 for c in cps if c["rt"] == 1),
        "cp_rt_values": sorted({c["rt"] for c in cps}), "cp_len_max": r["cp_len_max"],
        "cp_detail": [[c["schedule_id"], c["rt"]] for c in cps],
        "n_ev_click": len(ev_in), "n_ev": len(ev_first),
        "n_ev_orphan": len(ev_req) - len(ev_in),
        "ev_first": ev_first,
        "ev_req_tmin": min(ev_first.values()) if ev_first else None,
        "ev_req_tmax": max(ev_first.values()) if ev_first else None,
        "auto_vals": auto_vals, "choice_vals": choice_vals,
        "n_ev_choice": sum(1 for k, v in choice_vals.items() if v and k != "0" and k != ""
                           and k != "None"),
        "n_eff_new": eff_new,
        "dash_vals": dict(collections.Counter(str(x.get("dash")) for x in races)),
        "n_evq": len(r["evq"]), "n_evq_mini": sum(1 for v in r["evq"].values() if v["mini"]),
        "n_evq_succ": sum(1 for v in r["evq"].values() if v["succ"]),
        "n_tip_gain": tip_gain, "n_skill_gain": sk_gain,
        "n_race": len(races), "race_dev_sum": sum(dev), "race_dev_n": len(dev),
        "race_pop_dev": sum(devp), "race_pop_dev_n": len(devp),
        "race_skill_act": sum(x["skill_act"] or 0 for x in races
                              if x.get("skill_act") is not None),
        "race_dash": sum(1 for x in races if (x.get("dash") or 0) != 0),
        "race_first": sum(1 for x in races if x.get("rank") == 1),
        "race_pred_ambig": sum(1 for x in races if x["ambig"]),
        "race_no_base": sum(1 for x in races if not x.get("pred")),
        "hands": hs, "races": races,
    }


# ---------------------------------------------------------------- 统计（不拟合）

def ranks(v):
    n = len(v)
    order = sorted(range(n), key=lambda i: v[i])
    r = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman_perm(xs, ys):
    n = len(xs)
    if n < 4:
        return None
    rx, ry = ranks(xs), ranks(ys)
    mx = my = (n + 1) / 2.0
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if den == 0:
        return None
    obs = sum((rx[i] - mx) * (ry[i] - my) for i in range(n)) / den
    if math.factorial(n) <= 40320:
        null = sorted(sum((rx[i] - mx) * (ry[p[i]] - my) for i in range(n)) / den
                      for p in itertools.permutations(range(n)))
        meth = "穷举 %d 排列" % len(null)
    else:
        rnd = random.Random(20261105)
        null = []
        for _ in range(20000):
            p = list(range(n))
            rnd.shuffle(p)
            null.append(sum((rx[i] - mx) * (ry[p[i]] - my) for i in range(n)) / den)
        null.sort()
        meth = "置换 2 万次（种子 20261105）"
    p = 2.0 * sum(1 for v in null if abs(v) >= abs(obs) - 1e-12) / len(null)
    return {"n": n, "rho": round(obs, 4), "p": round(min(1.0, p), 4), "method": meth}


def zscore(v):
    if len(v) < 2:
        return [0.0] * len(v)
    sd = st.pstdev(v)
    return [(x - st.mean(v)) / sd for x in v] if sd else [0.0] * len(v)


# ---------------------------------------------------------------- 报告

def report(cache, w):
    tbl = json.load(open(CONST, encoding="utf-8"))["five_status_final_score"]
    pool = cache.get("finish_pool") or {}
    rows = [run_row(rk, r, tbl, pool) for rk, r in cache["runs"].items()]
    rows.sort(key=lambda x: int(x["chara"]))
    full = [r for r in rows if r["tp_max"] and r["tp_max"] >= 78]
    part = [r for r in rows if r["chara"] not in {f["chara"] for f in full}]
    s = cache["stats"]

    def gs(k):
        return s.get(k) or 0        # 计数器缺项按 0 报，别打印 None

    # D5 事件分类要在逐局读数之前先做一遍（整局样本共识），再回灌成每局的随机事件数
    ev_turns = collections.defaultdict(dict)
    for r in full:
        for eid, t in r["ev_first"].items():
            ev_turns[eid][r["chara"]] = t
    fixed = {e for e, m in ev_turns.items() if len(set(m.values())) == 1 and len(m) >= 2}
    vary = {e for e, m in ev_turns.items() if len(set(m.values())) > 1}
    seen_once = {e for e, m in ev_turns.items() if len(m) < 2}
    for r in rows:
        r["n_ev_fixed"] = sum(1 for e in r["ev_first"] if e in fixed)
        r["n_ev_rand"] = sum(1 for e in r["ev_first"] if e in vary)
        r["n_ev_single"] = sum(1 for e in r["ev_first"] if e in seen_once)

    w("== Q0 样本与口径 ==")
    w("  报文 %s 个（解码失败 %s）｜带 chara_info 的响应 %s｜不带 chara_info 的响应 %s"
      "（终局结算包全部落在这一类）｜带结算 %s｜配对手 %s（其中结算包自身也带预告 %s）｜带因子事件 %s"
      % (gs("files"), gs("decode_fail"), gs("resp_with_ci"), gs("resp_no_ci"),
         gs("cr_seen"), gs("hand_paired"), gs("hand_r_also_board"),
         gs("resp_with_eff_fac")))
    w("  局数 %d｜整局（面板覆盖到 78＝引擎 77）%d 局：%s"
      % (len(rows), len(full), [r["chara"] for r in full]))
    w("  覆盖不全 %d 局：%s" % (len(part), [[r["chara"], r["tp_min"], r["tp_max"]] for r in part]))
    w("  请求计数：check_event=%s exec_command=%s race_end=%s race_start=%s continue=%s start=%s"
      "｜定局前的无主请求=%s"
      % (s.get("req:event"), s.get("req:cmd"), s.get("req:race_end"), s.get("req:race_start"),
         s.get("req:continue"), s.get("req:start"), s.get("req_orphan")))
    w("  轴：报文 turn ＝ 面板（1 基），引擎 ＝ 面板 − 1；本文逐回合一律双轴")
    w("  终局记录全局池：%d 条（来自 %d 个结算包）｜逐局表的官方分只按这两把键认局（D9）"
      % (len(pool), gs("fin_pkt")))

    w("\n== Q1 训练随机项：`command_result.result_state` 的档（D2）==")
    allh = [h for r in rows for h in r["hands"]]
    by = collections.defaultdict(list)
    for h in allh:
        by[str(h["state"])].append(h)
    for k in sorted(by):
        a = by[k]
        w("  state=%-5s n=%-4d Δ五维和 %s..%s 中位 %s｜Δ<0 %d｜Δ=0 %d｜"
          "预告 failure_rate 中位 %s 最高 %s｜预告五维和>0 %d/%d｜ΔSP 中位 %s｜Δ体力 中位 %s"
          % (k, len(a), min(h["d5"] for h in a), max(h["d5"] for h in a),
             int(st.median([h["d5"] for h in a])), sum(1 for h in a if h["d5"] < 0),
             sum(1 for h in a if h["d5"] == 0), int(st.median([h["fr"] for h in a])),
             max(h["fr"] for h in a), sum(1 for h in a if h["pred"] > 0), len(a),
             int(st.median([h["d_sp"] for h in a])), int(st.median([h["d_vital"] for h in a]))))
    w("  ⇒ result_state 全库取值 = %s（跨 %d 局 %d 手可归因的手）。"
      "state=1 的 Δ五维和 **%d/%d 为 0**、其余档为正 ⇒ 认 state=1 = 失败（D2）"
      % (sorted(by), len(rows), len(allh),
         sum(1 for h in by.get("1", []) if h["d5"] == 0), len(by.get("1", []))))
    fr1 = sorted([h["fr"] for h in by.get("1", [])], reverse=True)
    fr2 = sorted([h["fr"] for h in by.get("2", [])], reverse=True)
    w("  失败手的预告 failure_rate = %s｜成功手里 failure_rate>0 的手数 %d/%d（最高 %s）"
      % (fr1, sum(1 for x in fr2 if x > 0), len(fr2), fr2[0] if fr2 else None))
    w("  ⇒ 报文里**没有训练第三档**：`command_result` 只有 {command_id, sub_id, result_state} 三键，"
      "「训练大成功」在报文侧不可识别（详见 Q8-1）")
    w("  逐局手档位取值：%s" % {r["chara"]: r["state_values"] for r in rows})

    w("\n== Q1b 与主线已核过的配对结果对账（`out/_hand_gain.json`，只读）==")
    try:
        HG = json.load(open("out/_hand_gain.json", encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        HG = None
        w("  ⚠ 读不到 `out/_hand_gain.json`（%s）⇒ 本节无法对账。" % e)
    if HG:
        mine = {(str(h["run"]), int(h["turn"]), h["file"]): h for h in HG}
        ovs = {(str(r["chara"]), h["t_panel"], h["file_R"]): h for r in rows for h in r["hands"]}
        both = [k for k in mine if k in ovs]
        bad = [k for k in both if mine[k]["state"] != ovs[k]["state"]
               or sum(mine[k]["delta"]) != ovs[k]["d5"]
               or mine[k]["board_file"] != ovs[k]["file_P"]]
        w("  对方（11-03 留档，报文当时比现在少）%d 手｜本文 %d 手｜(局,面板回合,结算包) 同键 %d 手"
          % (len(mine), len(ovs), len(both)))
        w("  同键里 state / Δ五维和 / P 包名 三项**全部一致**的手数 %d/%d（不一致 %d）%s"
          % (len(both) - len(bad), len(both), len(bad),
             [(k, mine[k]["state"], ovs[k]["state"], sum(mine[k]["delta"]), ovs[k]["d5"],
               mine[k]["board_file"], ovs[k]["file_P"]) for k in bad[:6]]))
        w("  只在对方（本文漏配，含 11-03 后被覆盖的回合）：%d 手，按局 %s"
          % (len([k for k in mine if k not in ovs]),
             dict(collections.Counter(k[0] for k in mine if k not in ovs))))
        w("  只在本文（新增报文 / 或本文允许结算包自带预告的口径差）：%d 手，按局 %s；"
          "其中结算包自身也带预告 %d 手（hand_gain 会跳过这种回合）"
          % (len([k for k in ovs if k not in mine]),
             dict(collections.Counter(k[0] for k in ovs if k not in mine)),
             sum(1 for h in ovs.values() if h.get("r_board"))))

    w("\n== Q2 逐局表（双轴；运气档可观测量）==")
    w("  %-5s %-8s %-8s %-5s %-7s %-12s %-6s %-7s %-5s %-5s %-7s %-7s %-6s %-6s %-6s %-6s "
      "%-6s %-6s %-6s %-6s %-6s %-9s %-5s %-5s %-5s"
      % ("run", "t面板", "t引擎", "快照", "五维分", "官方分(候选)", "SP手", "SP回合", "手", "失败",
         "期望失败", "L1", "CP/大", "事件", "固定", "随机", "点击", "队列", "小游戏", "提示",
         "技能", "赛", "L3(可比)", "dash≠0", "第1"))
    for r in rows:
        w("  %-5s %-8s %-8s %-5s %-7s %-12s %-6s %-7s %-5s %-5s %-7.2f %-7.2f %-6s %-6s %-6s "
          "%-6s %-6s %-6s %-6s %-6s %-6s %-6s %-9s %-5s %-5s"
          % (r["chara"], "%s-%s" % (r["tp_min"], r["tp_max"]),
             "%s-%s" % (r["te_min"], r["te_max"]), r["n_snap"], r["score_end"],
             ("%s(%s,%d/%d)" % (r["rank_score"], r["rank_join"], r["rank_cand"],
                                r["rank_five_match"]))
             if r["rank_score"] is not None else "没读到(候选%d)" % r["rank_cand"],
             r["sp_hand"], r["sp_turn"], r["n_hand"], r["n_fail"], r["exp_fail"], r["L1"],
             "%d/%d" % (r["n_cp"], r["n_great"]), r["n_ev"], r["n_ev_fixed"], r["n_ev_rand"],
             r["n_ev_click"], r["n_evq"], r["n_evq_mini"], r["n_tip_gain"], r["n_skill_gain"],
             r["n_race"], "%s(n%d)" % (r["race_dev_sum"], r["race_dev_n"]),
             r["race_dash"], r["race_first"]))
    w("  整局 %d 局散布：五维分 %s｜SP手 %s｜官方分 %s（候选数/五维副键吻合数见上表）"
      % (len(full), [r["score_end"] for r in full], [r["sp_hand"] for r in full],
         [r["rank_score"] for r in full]))
    w("  注：`官方分(接法,主键候选数/其中终局五维逐轴也吻合的个数)`；主键 = 该局继承对 "
      "`succession_trained_chara_id_1/_2`（#54 定案），主键不唯一时**只允许**再用「继承对相同 + "
      "终局五维逐轴相同」这一把副键收口（仍不唯一 ⇒ 报没读到，不猜）。终局记录取自**全局池**"
      "（结算包常在下一局开局后才到，按时间序归局会错档；修订原因见 D9）。SP回合 会被技能花费压低，"
      "只作 SP手 的对照。")
    w("  ⚠ `五维分` 取该局**最后一条**快照：终点截断的局 %s 该列**不是终分**（只作形状）；"
      "起点截断的整局 %s（报文从面板 %s 起）其手/事件/失败计数只覆盖那一段 ⇒ 这两个缺陷都随 Q7 "
      "的 9 局一起进来，读关联强度时记得它们不是全零起点。"
      % ([r["chara"] for r in part], [r["chara"] for r in full if r["tp_min"] > 1],
         [r["tp_min"] for r in full if r["tp_min"] > 1]))

    w("\n== Q3 按回合分层（D1，末段单独）：失败手数 / 期望失败 / 手数 ==")
    for nm, a, b in SEG:
        f = sum(r["fail_by_seg"][nm][0] for r in rows)
        e = sum(r["fail_by_seg"][nm][1] for r in rows)
        n = sum(r["fail_by_seg"][nm][2] for r in rows)
        f2 = sum(r["fail_by_seg"][nm][0] for r in full)
        e2 = sum(r["fail_by_seg"][nm][1] for r in full)
        n2 = sum(r["fail_by_seg"][nm][2] for r in full)
        w("  %-8s 面板%-7s 引擎%-8s 全部局 失败%d/期望%.2f/手%d ｜ 整局%d局 失败%d/期望%.2f/手%d"
          % (nm, "%d-%d" % (a, b), "%d-%d" % (a - 1, b - 1), f, e, n, len(full), f2, e2, n2))
    w("  逐局 L1（实际−期望，正=比期望多失败＝运气差）：%s" % {r["chara"]: r["L1"] for r in rows})
    w("  逐局 失败/期望/手：%s" % {r["chara"]: [r["n_fail"], r["exp_fail"], r["n_hand"]]
                                 for r in rows})

    w("\n== Q4 発表会档（D3 result_type 1=成功 / 2=大成功）==")
    for r in rows:
        w("  %-5s 数组最长 %d 条｜档 %s（取值 %s）⇒ 大成功 %d｜成功 %d"
          % (r["chara"], r["cp_len_max"], r["cp_detail"], r["cp_rt_values"],
             r["n_great"], r["n_success_cp"]))
    cps = [c[1] for r in rows for c in r["cp_detail"]]
    if cps:
        w("  跨局 %d 场的档分布：%s ⇒ 大成功 %.1f%%（#16 记真机约 54%%/场）"
          % (len(cps), dict(collections.Counter(cps)),
             100.0 * sum(1 for c in cps if c == 2) / len(cps)))
    w("  ⚠ `check_point_result_array` 全库最长 %d 条 ⇒ 第 4 场的档在这批报文里读不到（见 Q8-7）"
      % max(r["cp_len_max"] for r in rows))
    w("  ⚠ 発表会**不按回合分层**：`check_point_result_array[]` 只给 `schedule_id` + `result_type`，"
      "场次落哪个回合要另做 schedule 对表（#16 记第 4 场在面板 77）⇒ 「大成功数的末段分层」本文判不了；"
      "训练的第三档本来就不可识别（Q8-1），也只有失败数能分层（Q3）。")

    w("\n== Q5 事件腿（D4/D5）==")
    w("  整局 %d 局播放过的 `event_id` 去重 %d 个：逐局首次回合恒定 %d｜会变 %d｜只在 1 局出现过（判不了）%d"
      % (len(full), len(ev_turns), len(fixed), len(vary), len(seen_once)))
    w("  回合恒定的（剧本固定项，前 14 个）：%s"
      % {e: list(ev_turns[e].values())[0] for e in sorted(fixed, key=str)[:14]})
    w("  回合会变的（随机项，前 12 个，逐局面板首次回合）：")
    for e in sorted(vary, key=str)[:12]:
        w("    event_id=%-8s %s" % (e, ev_turns[e]))
    w("  每局：事件数(去重) %s｜其中随机项 %s｜点击条数(check_event 请求) %s"
      % ({r["chara"]: r["n_ev"] for r in rows}, {r["chara"]: r["n_ev_rand"] for r in rows},
         {r["chara"]: r["n_ev_click"] for r in rows}))
    w("  轴核对：check_event 请求 `current_turn` 的首见回合区间 vs 该局 `chara_info.turn` 区间："
      "（应同侧；区间外的请求按无主剔除）")
    for r in rows:
        if r["n_ev"]:
            w("    %-5s 事件首次回合 %s–%s｜快照 turn %s–%s｜剔出 %d 条点击"
              % (r["chara"], r["ev_req_tmin"], r["ev_req_tmax"], r["tp_min"], r["tp_max"],
                 r["n_ev_orphan"]))
    w("  `exec_auto_play_plan_id` 取值分布（**语义未证**，可能是自动播放方案 id，不等于整局托管）：")
    w("    %s" % {r["chara"]: r["auto_vals"] for r in rows if r["n_ev_click"]})
    nz = sorted({k for r in rows for k in r["auto_vals"] if k not in ("0", "", "None")})
    w("  ⇒ 非零 plan 取值 = %s；占比（非零点击/该局点击）%s"
      % (nz, {r["chara"]: round(1.0 * sum(v for k, v in r["auto_vals"].items() if k != "0")
                                 / max(1, r["n_ev_click"]), 2)
              for r in rows if r["n_ev_click"]}))
    w("     **不能据此判手点/托管**（字段语义未证，也可能是事件类型差异）；它与"
      "「179 那批是用户手点」这条 11-05 更正**不矛盾**，但不构成证据。")
    w("  `choice_number` 取值分布（玩家真按过的选择）：%s"
      % {r["chara"]: r["choice_vals"] for r in rows if r["n_ev_click"]})
    w("  待播队列去重 %s｜其中小游戏 %s｜继承事件 %s（队列 ≠ 播放）"
      % ({r["chara"]: r["n_evq"] for r in rows}, {r["chara"]: r["n_evq_mini"] for r in rows},
         {r["chara"]: r["n_evq_succ"] for r in rows}))
    w("  每局新增 `chara_effect_id_array` 条目数：%s" % {r["chara"]: r["n_eff_new"] for r in rows})

    w("\n== Q6 比赛腿（D6）==")
    for r in rows:
        if not r["n_race"]:
            continue
        w("  %-5s 赛 %d 场｜实际 %s｜预测(五维和序) %s｜L3 %s (可比 n=%d，基线不唯一 %d，无基线 %d)"
          "｜第1 %d｜人气偏离 %s (n=%d)｜技能发动 %d｜dash≠0 %d"
          % (r["chara"], r["n_race"], [x["rank"] for x in r["races"]],
             [x["pred"] for x in r["races"]], r["race_dev_sum"], r["race_dev_n"],
             r["race_pred_ambig"], r["race_no_base"], r["race_first"],
             r["race_pop_dev"], r["race_pop_dev_n"], r["race_skill_act"], r["race_dash"]))
    rs = [x for r in rows for x in r["races"] if x.get("pred") and x.get("rank") and not x["ambig"]]
    w("  跨局可比基线的赛 %d 场：预测=实际 %d｜实际比预测好 %d｜实际比预测差 %d"
      % (len(rs), sum(1 for x in rs if x["pred"] == x["rank"]),
         sum(1 for x in rs if x["pred"] > x["rank"]),
         sum(1 for x in rs if x["pred"] < x["rank"])))
    w("  ⚠ 逐局 `skill_activate_count` 合计 = %s ⇒ 201–204（自选比赛刷局）**全为 0**："
      "这几场到底有没有真跑过程，报文侧判不了（名次几乎恒 1、`bashin_diff_from_top` 恒 0），"
      "**不能**读成「那 4 局没带技能」。" % {r["chara"]: r["race_skill_act"] for r in rows})
    w("  ⚠ 这批局是**赢家常驻**：跨局可比 %d 场里 %d 场预测=实际 ⇒ L3（名次偏离）在这批样本"
      "几乎没有散布，它能不能反映运气要换靶局才能判。" % (len(rs), sum(1 for x in rs if x["pred"] == x["rank"])))
    w("  `start_dash_state` 全库取值分布（语义未证，不翻成「出迟」）：%s"
      % dict(collections.Counter(str(x.get("dash")) for r in rows for x in r["races"])))
    w("  `last_straight_line_rank`（终直名次）取值分布：%s"
      % dict(collections.Counter(str(x.get("lsr")) for r in rows for x in r["races"])))
    w("  随机字段：`race_start_info.{random_seed,season,weather,sub_weather,ground_condition}`、"
      "`race_result_array[].{frame_order,popularity,result_rank,skill_activate_count,"
      "start_dash_state,last_straight_line_rank,bashin_diff,bashin_diff_from_top}`")
    w("  同场多次快照（重开/多 seed）最多的几局：%s"
      % {r["chara"]: sorted([x.get("pre_snaps") or 0 for x in r["races"]], reverse=True)[:6]
         for r in rows if r["n_race"]})
    w("  逐场明细（run｜面板t｜引擎t｜program｜预测｜实际｜人气｜闸位｜技能发动｜dash｜终直｜距头马马身差）：")
    for r in rows:
        for x in r["races"]:
            w("    %-5s t%-4s 引%-4s prog=%-7s pred=%-5s rank=%-4s pop=%-5s frame=%-4s "
              "act=%-4s dash=%-5s lsr=%-4s diff_top=%s"
              % (r["chara"], x.get("t"), (x.get("t") - 1) if x.get("t") else None,
                 x.get("prog"), x.get("pred"), x.get("rank"), x.get("pop"), x.get("frame"),
                 x.get("skill_act"), x.get("dash"), x.get("lsr"), x.get("diff_top")))

    w("\n== Q7 运气代理与终分的关联强度（D7/D8：只报强度，不折算）==")
    S = full
    outs = {"五维分": [r["score_end"] for r in S],
            "SP手": [float(r["sp_hand"]) for r in S],
            "五维分+SP手": [r["score_end"] + r["sp_hand"] for r in S]}
    if all(r["rank_score"] is not None for r in S):
        outs["官方分"] = [float(r["rank_score"]) for r in S]
    L = {"L1 −失败偏离": [-r["L1"] for r in S],
         "L2 大成功场数": [float(r["n_great"]) for r in S],
         "L3 名次偏离": [float(r["race_dev_sum"]) for r in S]}
    zs = zscore(L["L1 −失败偏离"])
    for v in [L["L2 大成功场数"], L["L3 名次偏离"]]:
        zs = [a + b for a, b in zip(zs, zscore(v))]
    L4 = {"L4a 技能发动(描述)": [float(r["race_skill_act"]) for r in S],
          "L4b 提示新增(描述)": [float(r["n_tip_gain"]) for r in S],
          "L4c 事件数(描述)": [float(r["n_ev"]) for r in S],
          "L4d 随机事件数(描述)": [float(r["n_ev_rand"]) for r in S],
          "L4e dash≠0(描述)": [float(r["race_dash"]) for r in S],
          "L4f 人气偏离(次要基线)": [float(r["race_pop_dev"]) for r in S]}
    w("  ⚠ L1/L2/L3 的取值分散度（先看有没有方差可解释）："
      "L1 %s｜L2 %s｜L3 %s" % ([r["L1"] for r in S], [r["n_great"] for r in S],
                               [r["race_dev_sum"] for r in S]))
    for name, lv in list(L.items()) + [("合成Z(L1+L2+L3 等权)", zs)] + list(L4.items()):
        for on, ov in outs.items():
            res = spearman_perm(lv, ov)
            if res:
                tag = "合成" if name.startswith("合成") else ("描述" if name.startswith("L4") else "气球")
                w("  ρ(%-22s, %-11s) = %+0.3f 双尾 p=%-8s n=%-3d [%s]（%s）"
                  % (name, on, res["rho"], res["p"], res["n"], res["method"], tag))
    w("  逐局 (L1, L2, L3) 与五维分/官方分/SP手：%s"
      % [[r["chara"], r["L1"], r["n_great"], r["race_dev_sum"], r["score_end"],
          r["rank_score"], r["sp_hand"]] for r in S])
    nf = len(S)
    w("  ⚠ 设计下限：整局样本 n=%d ⇒ 本设计双尾置换 p 的最小可达值 = 2/%d! = %.6f；"
      "有并列的变量（L2/L3 常出现同值）实际下限更高 ⇒ p 到不了下限的都不是「已证」。"
      % (nf, nf, 2.0 / math.factorial(nf)))
    w("  ⚠ 混杂披露：L2 吃演目 Lv（手法/练度），L3 吃参赛日程与马群强度（决策），"
      "只有 L1 的「期望」与「实际」出自同一报文（`failure_rate` 与 `result_state`）"
      "⇒ L1 是本文里唯一不需要外部假设的运气残差。")
    w("  ⚠ 这 9 局不是同一配置：201–204 是自选比赛刷局（五维分 1.3–1.6 万，CP 全成功档），"
      "与 168/169/170/171/179（4.2–5.0 万）混在一起 ⇒ ρ 里必然带「这局是不是认真练」的决策差，"
      "**不能读成运气→分数**。分组另报见 Q7b。")

    w("\n== Q7b 分组读数（把刷局与认真局分开，才谈得上运气）==")
    serious = [r for r in S if r["score_end"] > 30000]
    grind = [r for r in S if r["score_end"] <= 30000]
    for nm, g in [("认真局", serious), ("刷局", grind)]:
        w("  %s n=%d 局 %s" % (nm, len(g), [r["chara"] for r in g]))
        w("    五维分 %s｜L1 %s｜L2 %s｜L3 %s｜失败手 %d｜期望失败 %.2f｜大成功场 %d/%d"
          % ([r["score_end"] for r in g], [r["L1"] for r in g],
             [r["n_great"] for r in g], [r["race_dev_sum"] for r in g],
             sum(r["n_fail"] for r in g), sum(r["exp_fail"] for r in g),
             sum(r["n_great"] for r in g), sum(r["n_cp"] for r in g)))
    for nm, g in [("认真局", serious), ("刷局", grind)]:
        if len(g) >= 4:
            tg = [("五维分", [r["score_end"] for r in g])]
            if all(r["rank_score"] is not None for r in g):
                tg.append(("官方分", [float(r["rank_score"]) for r in g]))
            for on, ov in tg:
                for name, lv in [("L1 −失败偏离", [-r["L1"] for r in g]),
                                 ("L2 大成功场数", [float(r["n_great"]) for r in g]),
                                 ("L3 名次偏离", [float(r["race_dev_sum"]) for r in g])]:
                    res = spearman_perm(lv, ov)
                    if res:
                        w("    ρ(%s, %s) = %+0.3f p=%s n=%d ⇒ 该组 p 下限 2/%d! = %.4f"
                          % (name, on, res["rho"], res["p"], res["n"], res["n"],
                             2.0 / math.factorial(res["n"])))
                    elif name == "L2 大成功场数":
                        w("    %s 在%s组内**无方差**（全组同值）⇒ 该组无法判它与 %s 的关联"
                          % (name, nm, on))
    w("\n== Q8 报文里判不了的随机项（逐条给字段级理由，不猜）==")
    ks = cache.get("keysets") or {}
    sp = cache.get("settle_paths") or {}
    if not ks or not sp:
        w("  ⚠ 缓存里没有字段清单（旧缓存）⇒ 本节需重扫报文才能出证据。")
    ALLP = sorted(set(list(sp.keys()) + sum(ks.values(), [])))

    def leaf(p):
        return p.split(".")[-1].split("[")[-1]

    def kwlbl(pat):
        return sorted(p for p in ALLP if re.search(pat, leaf(p), re.I))

    states = {k.split(":", 1)[1]: v for k, v in s.items() if k.startswith("cr_state:")}
    w("  Q8-1 训练**大成功**档：判不了。`command_result` 整条只有这些键：%s；"
      "全库 %d 条结算的 `result_state` 取值分布 = %s ⇒ 只有两档，没有第三档。"
      % (ks.get("command_result"), gs("cr_seen"), dict(sorted(states.items()))))
    w("       全字段清单 %d 条路径里，叶子名像档位的候选（state|type|tier|rank|result|level）：%s"
      % (len(ALLP), kwlbl(r"state|type|tier|rank|result|level")))
    w("       叶子名含 great|crit|burst|sudden|jackpot|upper 的路径：%s"
      % (kwlbl(r"great|crit|burst|sudden|jackpot|upper") or "无"))
    w("  Q8-2 友情/支援卡**当次是否触发**：判不了。预告板每条只有这些键：%s / %s；"
      "`params_inc_dec_info_array` 给的是**合计值**（含友情那一份），没有逐次触发标记。"
      % (ks.get("board:home_info"), ks.get("board:festa_data_set")))
    w("       板上有两个**旗标/数组**看着相关但语义未证：`is_upper_training_event_occurred`"
      "（本文只计数：整局出现该旗标的响应数 %s｜配上手数 %s）与 `predict_gain_act_gauge_array`"
      "（本文**未取**；`tools/hand_gain.py` 的 act_step 取的是另一条 "
      "`festa_data_set.festa_info.act_training_step`）。**都不进气球、不当机制。**"
      % ({r["chara"]: r["n_upp_resp"] for r in rows}, {r["chara"]: r["n_upp_hand"] for r in rows}))
    w("  Q8-3 卡面效果**当次判定**：判不了。`chara_effect_id_array` 出现在 `chara_info`，"
      "只给**已持有**的效果集合（长度差=新增），无触发次数。")
    w("  Q8-4 事件**单个收益**：判不了。`unchecked_event_array[]` 的键：%s ⇒ 能数事件条数与首放回合，"
      "但同回合多条事件时无法把增益分到具体事件（只有回合级 Δ）。"
      % ks.get("unchecked_event_array[]"))
    w("  Q8-5 提示（tips）**触发次数**：只能取集合长度差（`skill_tips_array`），"
      "同回合内既获得又消失会被抵消 ⇒ 报的是「新增数」不是「触发数」。")
    w("  Q8-6 比赛的**预期分**：判不了。`race_start_info` 键：%s；"
      "`race_result_array[]` 键：%s ⇒ 名次/闸位/dash/马身差可读，但报文没给「按名次本该得多少分」"
      "以外的期望，`random_seed` 也只是**收到**的值，生成规则在报文侧不可判。"
      % (ks.get("race_start_info"), ks.get("race_start_info.race_horse_data[].race_result_array[]")))
    w("  Q8-7 第 4 場発表会的档：判不了。全库 `check_point_result_array` 最长 %d 条"
      "（逐局最大 %s）⇒ 逐局表的「大成功数」只覆盖前 3 場，且它的键只有 %s。"
      % (max([r["cp_len_max"] for r in rows] or [0]),
         {r["chara"]: r["cp_len_max"] for r in rows},
         ks.get("festa_data_set.check_point_result_array[]")))
    w("  Q8-8 托管 / 手点：判不了。`exec_auto_play_plan_id` 的语义未证实（取值分布见 Q5），"
      "且**锚点局是用户手点**这件事来自 11-05 用户亲口补的更正，不是报文证明的。")
    w("  Q8-9 游戏内的「运气」维度（若存在）：判不了。全字段清单里叶子名含 luck|fortune|unluck 的路径：%s"
      % (kwlbl(r"luck|fortune|unluck") or "无"))
    w("  Q8-10 终局结算的键：%s / %s ⇒ 官方分 `rank_score` 可读，但**运气分**不在其中（若游戏有）。"
      % (ks.get("single_mode_finish_common"), ks.get("single_mode_finish_common.trained_chara[]")))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--txt", default=OUT_TXT)
    ap.add_argument("--csv", default=OUT_CSV)
    ap.add_argument("--json", default=OUT_JSON)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    os.chdir(REPO)
    os.makedirs("out", exist_ok=True)
    if a.from_cache and os.path.exists(a.cache):
        cache = json.load(open(a.cache, encoding="utf-8"))
        print("读缓存 %s（未重扫报文；files=%s）" % (a.cache, cache["stats"].get("files")))
    else:
        t0 = time.time()
        cache = scan(a.limit)
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
        print("扫描用时 %.0fs → %s" % (time.time() - t0, a.cache))
    buf = []

    def w(line=""):
        buf.append(line)
        print(line)

    rows = report(cache, w)
    open(a.txt, "w", encoding="utf-8").write("\n".join(buf) + "\n")
    cols = [k for k in rows[0].keys() if k not in ("hands", "races")]
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(cols)
        for r in rows:
            wr.writerow([json.dumps(r.get(c), ensure_ascii=False)
                         if isinstance(r.get(c), (list, dict)) else r.get(c) for c in cols])
    json.dump({"stats": cache["stats"],
               "rows": [{k: v for k, v in r.items() if k not in ("hands",)} for r in rows],
               "hands": {str(r["chara"]): r["hands"] for r in rows},
               "races": {str(r["chara"]): r["races"] for r in rows}},
              open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n逐局 %d 行 → %s｜读数 → %s｜摘要 → %s" % (len(rows), a.csv, a.txt, a.json))


if __name__ == "__main__":
    main()
