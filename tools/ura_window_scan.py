#!/usr/bin/env python3
"""URA 收尾期窗口报文核对（#URA-window A 阶段）：只用一手报文，逐 (run, 引擎回合) 问四条签名。

一条命令重建全部表：

    python tools/ura_window_scan.py
    python tools/ura_window_scan.py --viewer 238107205 --scen 15

**回合索引口径**（§0 用报文自证，不靠记忆）：
  报文 `chara_info.turn` = 面板显示值 = master.mdb `single_mode_turn.turn` = **1 基（1–78）**
  引擎 `base.turn` / 本仓 memo 的 `turn N` = **0 基** ⇒ 引擎回合 = 报文回合 − 1
  ⇒ 现实现 `67..=76` ⇔ 面板 `68..77`；memo 字面 `68..=77` ⇔ 面板 `69..78`

主线问的四条（全部 keyed on 引擎回合，每条给 支持 / 违例 / 判不了 三态）：
  Q1 训练**失败**：引擎 67（面板 68，本番当回合）到底有没有失败过？两个口径分开——
       Q1a 板上 `failure_rate>0`（显示层，能证伪「−100%」）；
       Q1b 结算 `result_state`（实际层）：**只当反证用**（fr>0 的手没失败可能只是运气，不算支持）。
  Q2 **演目 Lv**：所选训练对应演目 +1 从引擎 67 / 68 / 72 哪个开始、有没有落在引擎 77。
       perk 判据 = Lv +1 而**槽没被吐槽**（前后 `act_gauge` 都 ≥ `need_act_gauge`）：
       正常升级要花槽，花槽的 +1 不算 perk。触顶（`lv >= max_act_level`）记「判不了」。
  Q3 **体力 / 干劲增量**：引擎 67 的 +30/+1 是本番只入一次，还是报文里看得到两次？
       CP3 按 `data/scenario_matsuri.json` 应发 vital 30 / motivation 1 / 五维 / PT；
       同回合若还有训练手：`多出来 = 回合Δ体力 − 手上预告 − 30(本番)`
       ⇒ ≈0 只入一次；≈+30 报文里确实两次。Q3b「单手残差」是审计位（残差 ≈0 只说明
          +30 若存在是被算进预告里的，不能用来定边界 ⇒ 不进 §9 裁决）。
  Q4 引擎 **77（面板 78，第 4 场）** 有没有训练决策与 Lv+1：直接数该局该回合有没有结算手。

辅助（解释 onset，不当窗口证据）：
  K1 板上只剩稽古格 5101–5105；K2 `act_training_state` 全 2 且合宿剩余 = 0
  P1 同局**同格**体力预告逐回合序列 + 台阶（memo 的每手 +30 若存在应在预告里）
  P2 逐回合哪些演目 Lv 变了（单格 +1 = 手/perk；多格齐 +1 = 合宿结束）

单判三条（§11–§13，主线 11-04 追加）：
  §11 面板 68–72（引擎 67–71）逐 (run,turn) 双证：板上有普通格 + 当回合实际取的手。
  §12 体力+30：结算层残差（窗内实际手）＋ 预告层同格段中位差（合宿/窗外/窗内三段）。
       ⇒ 两层都对不上 +30 就判「作为常数否证」；残差里的 ≈+30 样本要单独看是不是失败退费。
  §13 演目 Lv+1：稽古手按面板 73 边界拆两段，分「花槽正常升级 / 不花槽 perk / 没升 / 触顶」。
       ⇒ 若窗外也 +1 但都花槽、窗内 +1 全不花槽 ⇒ Lv+1(不扣槽) 是收尾期专属，不是稽古本有。

**已定裁决（本脚本 11-04 复算口径）**：收尾期 = 面板 73–78 = **引擎 72..=77**；
memo 字面 68..=77 与现实现 67..=76 的左沿分别偏早 4 / 5 回合，现实现右沿少 1 回合。

红线：不跑引擎、不产常数、不写 `data/`。表侧（period / training_set_id / race_entry_type 与跨剧本
      对照）的事实源是主线 `tools/ura_period_scan.py` → `out/_ura_period_scan_20261104.txt`，
      本脚本只做报文侧，不重做表侧。
产物：`out/_ura_window_turns.csv`（逐 run×引擎回合全量）/ `out/_ura_window_report.txt`。
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "data", "scenario_matsuri.json")
OUT_CSV = os.path.join(ROOT, "out", "_ura_window_turns.csv")
OUT_TXT = os.path.join(ROOT, "out", "_ura_window_report.txt")

AXES = ("speed", "stamina", "power", "guts", "wiz")
PLAIN = {101, 102, 103, 104, 105, 106}
KEIKO = {5101, 5102, 5103, 5104, 5105}
# 演目槽上限：`crates/matsuri-core/src/game/mod.rs:33 pub const ACT_GAUGE_MAX: i32 = 5`
# （只读引用来判断引擎的 `lit` 条件，不是本文产出的常数）
GAUGE_MAX = 5
ACT_OF = {5101: 1, 5102: 2, 5103: 3, 5104: 4, 5105: 5, 101: 1, 102: 2, 103: 3, 104: 4, 105: 5}
ENG_CUR = list(range(67, 77))                 # 现实现 67..=76
ENG_MEMO = list(range(68, 78))                # memo 字面 68..=77
ENG_SHOW = list(range(60, 78))
RACE_KEYS = ("race_history", "race_reward_info", "trophy_reward_info")
STEP_MIN = 15                                  # 纯显示过滤：同格相邻两次变化小于它不列
CAND = [("现实现 67..=76", ENG_CUR), ("memo 字面 68..=77", ENG_MEMO),
        ("报文形状 72..=77", list(range(72, 78)))]
ADJ = ["Q1a_显示失败率", "Q1b_实际失败", "Q2_演目Lv加一", "Q3a_干劲加一",
       "K1_只剩稽古", "K2_稽古态非合宿"]       # §9 参裁；Q3b 只做审计


def short(run, viewer):
    return run.replace("%s@" % viewer, "@")


# ---------------------------------------------------------------- 报文侧

def acts_of(data):
    out = {}
    for a in ((data.get("festa_data_set") or {}).get("act_info_array")) or []:
        if isinstance(a, dict) and a.get("act_type") is not None:
            out[int(a["act_type"])] = {"lv": a.get("level"), "gauge": a.get("act_gauge"),
                                       "need": a.get("need_act_gauge"),
                                       "state": a.get("act_training_state")}
    return out


def cells_of(src):
    """预告板一条链：command_id → 等级 / 失败率 / 五维预告 / 体力预告 / 人头。"""
    out = {}
    for c in src or []:
        cid = c.get("command_id")
        if cid is None or cid not in (PLAIN | KEIKO):
            continue
        p5 = pv = 0
        for e in c.get("params_inc_dec_info_array") or []:
            tt = e.get("target_type")
            if tt in (1, 2, 3, 4, 5):
                p5 += e.get("value") or 0
            elif tt == 10:
                pv += e.get("value") or 0
        out[cid] = {"level": c.get("level"), "fr": c.get("failure_rate"),
                    "p5": p5, "pv": pv, "heads": len(c.get("training_partner_array") or [])}
    return out


def rec_of(name, data, ci):
    fi = (data.get("festa_data_set") or {}).get("festa_info") or {}
    cr = data.get("command_result")
    cr = cr if isinstance(cr, dict) else {}
    return {"file": name, "turn": ci.get("turn"), "vital": ci.get("vital"),
            "mot": ci.get("motivation"), "max_vital": ci.get("max_vital"),
            "sp": ci.get("skill_pt"), "five": [ci.get(k) or 0 for k in AXES],
            "camp_left": fi.get("summer_camp_remain_turn_num"),
            "max_act": fi.get("max_act_level"),
            "n_cp": len((data.get("festa_data_set") or {}).get("check_point_result_array") or []),
            "acts": acts_of(data),
            "home": cells_of((data.get("home_info") or {}).get("command_info_array")),
            "festa": cells_of((data.get("festa_data_set") or {}).get("command_info_array")),
            "cmd": cr.get("command_id"), "state": cr.get("result_state"), "sub": cr.get("sub_id"),
            "race": any(k in data for k in RACE_KEYS)}


def collect(viewer, scen, lo, hi):
    """run → 面板回合 → [报文按采集序]；只留带 chara_info 的 notify_response。"""
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    n = 0
    ci_keys = []
    for name in sorted(os.listdir(RAW)):
        if not name.endswith("notify_response.bin"):
            continue
        try:
            d = decode_file(os.path.join(RAW, name))
        except Exception:
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        v = d.get("viewer_id") or (d.get("data_headers") or {}).get("viewer_id")
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or ci.get("scenario_id") != scen or ci.get("turn") is None:
            continue
        if viewer and v != viewer:
            continue
        t = ci["turn"]
        if not (lo <= t <= hi):
            continue
        if not ci_keys:
            ci_keys = sorted(ci.keys())
        run = "%s@%s@%s" % (v, ci.get("single_mode_chara_id"), ci.get("start_time"))
        per[run][t].append(rec_of(name, data, ci))
        n += 1
    return per, n, ci_keys


# ---------------------------------------------------------------- 逐 (run, 引擎回合)

def pair(rs):
    """R = 本回合第一条 command_result(训练/稽古格)；P = R 之前最后一条带预告板的报文。"""
    ri = next((i for i, r in enumerate(rs) if r["cmd"] in (PLAIN | KEIKO)), None)
    if ri is None:
        return None, None
    pi = next((i for i in range(ri - 1, -1, -1) if rs[i]["home"] or rs[i]["festa"]), None)
    return (rs[pi] if pi is not None else None), rs[ri]


def merged(rec):
    """该报文预告板：格 → 五维/体力预告（home 链 + festa 链），另留两条链各自的体力预告。"""
    out = {}
    for cid in set(rec["home"]) | set(rec["festa"]):
        h, f = rec["home"].get(cid, {}), rec["festa"].get(cid, {})
        fr = h.get("fr") if h.get("fr") is not None else f.get("fr")
        out[cid] = {"p5": h.get("p5", 0) + f.get("p5", 0), "pv": h.get("pv", 0) + f.get("pv", 0),
                    "fr": fr, "level": h.get("level", f.get("level")),
                    "heads": h.get("heads", f.get("heads")), "ph": h.get("pv"), "pf": f.get("pv")}
    return out


def row_of(run, t, rs):
    et = t - 1
    first, last = rs[0], rs[-1]
    P, R = pair(rs)
    bd = merged(P or first)
    acts_p = (P or first)["acts"] or {}
    acts_r = (R or last)["acts"] or {}
    acts_0, acts_1 = first["acts"] or {}, last["acts"] or {}
    frs = [c["fr"] for c in bd.values() if c["fr"] is not None]
    hand = R is not None and R["cmd"] in bd
    cell = bd.get(R["cmd"]) if R else None
    d_vit = (R["vital"] or 0) - (P["vital"] or 0) if (hand and P) else None
    d_mot = (R["mot"] or 0) - (P["mot"] or 0) if (hand and P) else None
    d5_hand = sum(R["five"]) - sum(P["five"]) if (hand and P) else None
    resid_v = d_vit - cell["pv"] if (hand and d_vit is not None and cell) else None
    resid5 = d5_hand - cell["p5"] if (hand and d5_hand is not None and cell) else None

    actk = ACT_OF.get(R["cmd"]) if hand else None
    lv_b = lv_a = d_lv = g_b = g_a = need = None
    perk_lv = None
    if actk is not None and acts_p.get(actk) and acts_r.get(actk):
        b, a = acts_p[actk], acts_r[actk]
        if None not in (b["lv"], a["lv"]):
            lv_b, lv_a, d_lv = b["lv"], a["lv"], a["lv"] - b["lv"]
            g_b, g_a, need = b["gauge"], a["gauge"], b["need"]
            if lv_b >= (first["max_act"] or 99):
                pass                                     # 触顶 ⇒ 判不了
            elif d_lv and None not in (g_b, g_a, need) and g_b >= need and g_a >= need:
                perk_lv = 1                              # 升级没花槽
            else:
                perk_lv = 0

    lv_delta = []
    for k in sorted(set(acts_0) & set(acts_1)):
        if None not in (acts_0[k]["lv"], acts_1[k]["lv"]) and acts_1[k]["lv"] != acts_0[k]["lv"]:
            lv_delta.append("%s%+d" % (k, acts_1[k]["lv"] - acts_0[k]["lv"]))

    n_plain = sum(1 for c in bd if c in PLAIN)
    n_keiko = sum(1 for c in bd if c in KEIKO)
    camp_left = last["camp_left"]
    row = {
        "run": run, "panel_turn": t, "eng_turn": et, "n_pkt": len(rs),
        "vital_start": first["vital"], "vital_end": last["vital"], "max_vital": last["max_vital"],
        "mot_start": first["mot"], "mot_end": last["mot"],
        "d5_turn": sum(last["five"]) - sum(first["five"]),
        "sp_start": first["sp"], "d_sp_turn": (last["sp"] or 0) - (first["sp"] or 0),
        "n_cp_start": first["n_cp"], "n_cp_end": last["n_cp"],
        "cp_fired": int(last["n_cp"] > first["n_cp"]),
        "race": int(any(r["race"] for r in rs)),
        "n_cells": len(bd), "n_plain": n_plain, "n_keiko": n_keiko,
        "fr_max": max(frs) if frs else None, "n_fr_pos": sum(1 for f in frs if f and f > 0),
        "vital_at_board": (P or first)["vital"], "mot_at_board": (P or first)["mot"],
        "camp_left": camp_left, "max_act": last["max_act"],
        "state_all2": int(bool(acts_p) and all(a["state"] == 2 for a in acts_p.values())),
        "camp0": int((camp_left or 0) == 0),
        "keiko_only": int(n_keiko >= 3 and n_plain == 0),
        "act_lv_before": "|".join("%s:%s" % (k, acts_p[k]["lv"]) for k in sorted(acts_p)),
        "act_gauge": "|".join("%s:%s/%s" % (k, acts_p[k]["gauge"], acts_p[k]["need"])
                              for k in sorted(acts_p)),
        "lv_delta_turn": "|".join(lv_delta),
        "pred_pv": "|".join("%s:%s" % (k, bd[k]["pv"]) for k in sorted(bd)),
        "pred_pv_festa": "|".join("%s:%s" % (k, bd[k]["pf"]) for k in sorted(bd)
                                  if bd[k]["pf"] is not None),
        "cmd": R["cmd"] if R else None, "r_state": R["state"] if R else None,
        "r_sub": R["sub"] if R else None, "hand": int(hand),
        "pred5": cell["p5"] if (hand and cell) else None,
        "pred_pv_hand": cell["pv"] if (hand and cell) else None,
        "pred_pv_home": cell["ph"] if (hand and cell) else None,
        "pred_pv_festa_hand": cell["pf"] if (hand and cell) else None,
        "fr_chosen": cell["fr"] if (hand and cell) else None,
        "d_vital_hand": d_vit, "resid_vital": resid_v, "resid_five": resid5,
        "d_mot_hand": d_mot, "d_mot_turn": (last["mot"] or 0) - (first["mot"] or 0),
        "d_vital_turn": (last["vital"] or 0) - (first["vital"] or 0)
        if None not in (last["vital"], first["vital"]) else None,
        "act_of_cmd": actk, "lv_before": lv_b, "lv_after": lv_a, "d_lv": d_lv,
        "gauge_before": g_b, "gauge_after": g_a, "need_gauge": need,
        "lv_capped": int(lv_b is not None and lv_b >= (first["max_act"] or 99)),
        "perk_lv": perk_lv,
    }
    row["finale_shape"] = int(row["keiko_only"] and row["state_all2"] and row["camp0"])
    return row


# ---------------------------------------------------------------- 三态判定

def sig(row, key, ctx):
    """1 支持 / 0 违例 / None 判不了。"""
    if row is None:
        return None
    if key == "K1_只剩稽古":
        return row["keiko_only"] if row["n_cells"] else None
    if key == "K2_稽古态非合宿":
        return row["finale_shape"] if row["n_cells"] else None
    if key == "Q1a_显示失败率":
        if row["fr_max"] is None:
            return None
        if row["fr_max"] > 0:
            return 0                                   # 板上仍报 >0 ⇒ 该回合没被 −100%
        ref = ctx["fr_ref"].get(row["run"])
        if ref is None or row["vital_at_board"] is None:
            return None                                # 该局从没报过 >0 ⇒ 全 0 不算证据
        return 1 if row["vital_at_board"] <= ref else None
    if key == "Q1b_实际失败":
        if not row["hand"] or row["r_state"] is None:
            return None
        return 0 if row["r_state"] in ctx["bad_state"] else None
    if key == "Q2_演目Lv加一":
        if not row["hand"] or row["perk_lv"] is None:
            return None
        return row["perk_lv"]
    if key == "Q3a_干劲加一":
        if not row["hand"] or row["d_mot_hand"] is None:
            return None
        if row["mot_at_board"] is not None and row["mot_at_board"] >= ctx["mot_cap"]:
            return None                                # 板前满档，+1 被吃掉
        return int(row["d_mot_hand"] >= 1)
    if key == "Q3b_体力残差审计":
        if row["resid_vital"] is None:
            return None
        if row["vital_end"] is not None and row["max_vital"] and row["vital_end"] >= row["max_vital"]:
            return None                                # 顶上限，夹掉了
        if abs(row["resid_vital"] - 30) <= 8:
            return 1
        return 0 if abs(row["resid_vital"]) <= 3 else None
    return None


SIGS = ["K1_只剩稽古", "K2_稽古态非合宿", "Q1a_显示失败率", "Q1b_实际失败",
        "Q2_演目Lv加一", "Q3a_干劲加一", "Q3b_体力残差审计"]


def onset(by, run, key, ctx):
    js, vio = [], []
    for et in sorted(by[run]):
        h = sig(by[run][et], key, ctx)
        if h is None:
            continue
        js.append((et, h))
        if not h:
            vio.append(et)
    if not js:
        return None, vio
    for et, h in js:
        if h and not [v for v in vio if v >= et]:
            return et, vio
    return None, vio


def tally(runs, by, key, ctx, win):
    sup = con = na = 0
    exc = []
    for r in runs:
        for et in win:
            row = by[r].get(et)
            if not row:
                continue
            h = sig(row, key, ctx)
            if h is None:
                na += 1
            elif h:
                sup += 1
            else:
                con += 1
                exc.append("%s@%d" % (short(r, ""), et))
    return sup, con, na, exc


def span(ts):
    ts = sorted(set(ts))
    if not ts:
        return ""
    out, a, b = [], ts[0], ts[0]
    for t in ts[1:]:
        if t == b + 1:
            b = t
            continue
        out.append(str(a) if a == b else "%d–%d" % (a, b))
        a = b = t
    out.append(str(a) if a == b else "%d–%d" % (a, b))
    return ",".join(out)


def parse_pred(s):
    out = []
    for part in (s or "").split("|"):
        if ":" in part:
            k, v = part.rsplit(":", 1)
            try:
                out.append((int(k), int(v)))
            except ValueError:
                pass
    return out


def rng(pairs):
    if not pairs:
        return "—"
    vs = [v for _, v in pairs]
    return "%d..%d" % (min(vs), max(vs)) if min(vs) != max(vs) else str(vs[0])


# ---------------------------------------------------------------- 主

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewer", type=int, default=238107205)
    ap.add_argument("--scen", type=int, default=15)
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=78)
    a = ap.parse_args()

    cfg = json.load(open(CFG, encoding="utf-8"))
    cps = {c["turn"]: c for c in cfg["check_points"]}
    cp3 = cps.get(67) or {}

    per, n_pkt, ci_keys = collect(a.viewer, a.scen, a.lo, a.hi)
    rows, by = [], collections.defaultdict(dict)
    for r in sorted(per):
        for t, rs in sorted(per[r].items()):
            row = row_of(r, t, rs)
            rows.append(row)
            by[r][row["eng_turn"]] = row

    need = set(range(65, 79))
    used, excl = [], []
    for r in sorted(per):
        have = {row["panel_turn"] for row in rows if row["run"] == r}
        miss = sorted(need - have)
        (used if not miss else excl).append((r, miss))
    runs = [r for r, _ in used]
    states = {row["r_state"] for row in rows if row["hand"] and row["r_state"] is not None}
    bad = {s for s in states if s != 2}
    mot_cap = max((row["mot_end"] or 0) for row in rows)
    ctx = {"fr_ref": {}, "bad_state": bad or {1}, "mot_cap": mot_cap or 5}
    for r in runs:
        pos = [row["vital_at_board"] for row in by[r].values()
               if row["fr_max"] and row["fr_max"] > 0 and row["vital_at_board"] is not None]
        ctx["fr_ref"][r] = max(pos) if pos else None

    L = []
    w = L.append
    w("URA 收尾期窗口报文核对（A 阶段：报文定四签名，不跑引擎、不产常数）")
    w("报文 %s｜剧本 %d｜viewer %s｜notify_response %d 条｜局 %d（面板 65–78 齐 = 用 %d，排除 %d）"
      % (RAW, a.scen, a.viewer, n_pkt, len(per), len(runs), len(excl)))
    w("")

    w("== 0. 回合索引口径（报文自证）==")
    w("  chara_info 字段：%s" % ",".join(ci_keys))
    w("  引擎配置 check_points 的 turn（引擎 0 基）：%s" % sorted(cps))
    cp_on = collections.Counter(row["panel_turn"] for row in rows if row["cp_fired"])
    w("  报文里 check_point_result_array 变长的面板回合：%s" % sorted(cp_on))
    w("    ⇔ 配置 %s 里 23/47/67 = 面板回合 − 1 ⇒ **引擎回合 = 面板回合 − 1**" % sorted(cps))
    w("    注意 **CP4（配置 turn=77，面板 78）报文里从不长 n_cp**（面板 78 已无后续 notify）⇒ "
      "「CP4 = 引擎 77」只能靠配置与终局顺序推，不是报文可直接观测的事件")
    camp_on = sorted({row["panel_turn"] for row in rows if row["camp_left"] == 4})
    w("  合宿 camp_left=4 首现面板回合：%s（表侧合宿 61–64 ⇒ 引擎 60–63，与主线表侧核对同口径）" % camp_on)
    race_on = sorted({row["panel_turn"] for row in rows if row["race"]})
    w("  带 race_history/race_reward_info 的面板回合（**贯穿全季**，玩家整局都在跑自选/固定赛，"
      "不是收尾期专属锚点）：%s" % span(race_on))
    trio = {74: 0, 76: 0, 78: 0}
    for r in runs:
        for p in trio:
            if by[r].get(p - 1, {}).get("race"):
                trio[p] += 1
    w("    其中 URA 三场（引擎硬编码 ura_turns=[73,75,77] ⇒ 面板 74/76/78）在用的 %d 局里命中：%s "
      "⇒ 引擎 77 = 面板 78 = 最后一场，报文可证" % (len(runs), trio))
    w("  ⇒ 现实现 67..=76 ⇔ 面板 68..77；memo 字面 68..=77 ⇔ 面板 69..78；本番当回合 = 引擎 67 = 面板 68")
    w("  表侧（period / training_set_id / race_entry_type / 跨剧本对照）见主线 "
      "tools/ura_period_scan.py → out/_ura_period_scan_20261104.txt，本脚本不重做")
    w("")

    w("== 0b. 局的覆盖（边界判定要求面板 65–78 报文齐）==")
    for r in runs:
        v = sorted(by[r])
        w("  [用]   %-30s 引擎回合 %d–%d（%d 个）" % (short(r, a.viewer), v[0], v[-1], len(v)))
    for r, miss in excl:
        v = sorted(by[r])
        w("  [排除] %-30s 引擎回合 %d–%d；面板缺 %s ⇒ 不进矩阵"
          % (short(r, a.viewer), v[0], v[-1], span(miss)))
    w("")

    w("== 1. 逐 (run, 引擎回合) 三态矩阵（1 支持 / . 违例 / 空格 判不了）==")
    for key in SIGS:
        w("  -- %s --" % key)
        w("  %-30s%s" % ("引擎回合→", "".join("%4d" % t for t in ENG_SHOW)))
        for r in runs:
            line = "  %-30s" % short(r, a.viewer)
            for et in ENG_SHOW:
                h = sig(by[r].get(et), key, ctx)
                line += "      " if h is None else ("   %d" % h if h else "    .")
            w(line)
        w("")

    w("== 2. 聚合（分母 = 该引擎回合判得了的局数）==")
    w("  %-6s%-6s%s" % ("引擎t", "面板t", "".join("%-16s" % k for k in SIGS)))
    for et in ENG_SHOW:
        c, d = collections.Counter(), collections.Counter()
        for r in runs:
            row = by[r].get(et)
            if not row:
                continue
            for key in SIGS:
                h = sig(row, key, ctx)
                if h is None:
                    continue
                d[key] += 1
                c[key] += h
        w("  %-6d%-6d%s" % (et, et + 1,
                            "".join("%-16s" % ("%d/%d" % (c[k], d[k]) if d[k] else "–") for k in SIGS)))
    w("")

    w("== 3. Q1 训练失败一手 ==")
    w("  训练/稽古手的 result_state 值域：%s（非 2 的按「失败嫌疑」处理，只当反证）"
      % sorted(s for s in states if s is not None))
    for row in rows:
        if row["hand"] and row["r_state"] in ctx["bad_state"]:
            w("    嫌疑手：%-28s 面板 t%-3d 引擎 %-3d cmd %-5s state %s sub %s 板上 fr %s 体力 %s/%s 干劲 %s"
              % (short(row["run"], a.viewer), row["panel_turn"], row["eng_turn"], row["cmd"],
                 row["r_state"], row["r_sub"], row["fr_chosen"], row["vital_end"], row["max_vital"],
                 row["mot_end"]))
    for lo, hi, lab in ((67, 67, "引擎 67（本番当回合）"), (68, 71, "引擎 68–71（memo 争议段）"),
                        (72, 76, "引擎 72–76（报文形状段）"), (77, 77, "引擎 77（末回合）")):
        h = [row for row in rows if lo <= row["eng_turn"] <= hi and row["hand"]]
        f = [row for row in h if row["r_state"] in ctx["bad_state"]]
        pos = [row for row in rows if lo <= row["eng_turn"] <= hi and row["fr_max"] and row["fr_max"] > 0]
        w("  %-22s 有手 %3d 失败嫌疑手 %d｜板上 fr>0 的 (run,回合) %2d 回合 %s"
          % (lab, len(h), len(f), len(pos), span(row["eng_turn"] for row in pos) if pos else "—"))
    w("  板上仍报 failure_rate>0 的逐条（引擎 ≥64）：")
    for row in rows:
        if row["fr_max"] and row["fr_max"] > 0 and row["eng_turn"] >= 64:
            w("    %-28s 面板 t%-3d 引擎 %-3d fr_max %s%%（%d 格报 >0）板前体力 %s/%s 干劲 %s 只剩稽古 %d"
              % (short(row["run"], a.viewer), row["panel_turn"], row["eng_turn"], row["fr_max"],
                 row["n_fr_pos"], row["vital_at_board"], row["max_vital"], row["mot_at_board"],
                 row["keiko_only"]))
    w("")

    w("== 4. Q2 演目 Lv：每个训练手的 Lv / 槽（perk = 升级不花槽）==")
    w("  %-28s %-6s %-6s %-6s %-5s %-6s %-6s %-9s %-6s %s"
      % ("run", "面板t", "引擎t", "cmd", "演目", "Lv前", "Lv后", "槽前/need", "槽后", "判定"))
    for row in sorted((x for x in rows if x["hand"] and x["eng_turn"] >= 56),
                      key=lambda x: (x["run"], x["eng_turn"])):
        j = {1: "perk(+1 不花槽)", 0: "没升/花槽", None: "触顶⇒判不了"}[row["perk_lv"]]
        w("  %-28s %-6d %-6d %-6s %-5s %-6s %-6s %-9s %-6s %s"
          % (short(row["run"], a.viewer), row["panel_turn"], row["eng_turn"], row["cmd"],
             row["act_of_cmd"], row["lv_before"], row["lv_after"],
             "%s/%s" % (row["gauge_before"], row["need_gauge"]), row["gauge_after"], j))
    w("")

    w("== 4b. P2 逐回合哪些演目 Lv 变了（单格 +1 = 手/perk；多格齐 +1 = 合宿结束）==")
    for r in runs:
        hits = ["引擎 %d（面板 %d）%s 手%d cmd%s" % (et, et + 1, by[r][et]["lv_delta_turn"],
                                                    by[r][et]["hand"], by[r][et]["cmd"])
                for et in sorted(by[r]) if et >= 58 and by[r][et]["lv_delta_turn"]]
        w("  %-28s %s" % (short(r, a.viewer), " ｜ ".join(hits) or "引擎 58+ 无 Lv 变化"))
    w("")

    w("== 5. Q3 本番回合（引擎 67 = 面板 68）增量分解：30/1 入了一次还是两次 ==")
    w("  配置 CP3：vital %s motivation %s all_stat %s（成功档 %s）pt %s（成功档 %s）"
      % (cp3.get("vital"), cp3.get("motivation"), cp3.get("all_stat"), cp3.get("success_all_stat"),
         cp3.get("pt"), cp3.get("success_pt")))
    w("  %-28s %-6s %-6s %-4s %-7s %-7s %-7s %-6s %-9s %-7s %-7s %s"
      % ("run", "面板t", "引擎t", "手", "Δ体力", "预告", "残差", "本番", "多出来", "Δ干劲", "ΔPT", "Δ五维"))
    for row in sorted(rows, key=lambda x: (x["run"], x["eng_turn"])):
        if row["eng_turn"] not in (65, 66, 67, 68, 69, 72, 74, 76, 77):
            continue
        extra = ""
        if row["resid_vital"] is not None:
            extra = "%+d" % ((row["resid_vital"] - (cp3.get("vital") or 0))
                             if row["eng_turn"] == 67 else row["resid_vital"])
        w("  %-28s %-6d %-6d %-4d %-7s %-7s %-7s %-6s %-9s %-7s %-7s %+d"
          % (short(row["run"], a.viewer), row["panel_turn"], row["eng_turn"], row["hand"],
             row["d_vital_turn"], row["pred_pv_hand"], row["resid_vital"], row["cp_fired"], extra,
             row["d_mot_turn"], row["d_sp_turn"], row["d5_turn"]))
    w("  读法：非 67 行的「多出来」= 单手残差（有没有额外 +30）；67 行的「多出来」= 回合Δ − 预告 − 30(本番)，"
      "≈0 ⇒ 本番只入一次，≈+30 ⇒ 报文里两次（重复入账）。体力顶上限会夹掉 Δ，见 §10。")
    w("")

    w("== 6. Q4 引擎 77（面板 78，第 4 场）有没有训练决策与 Lv+1 ==")
    nh = nb = 0
    for r in runs:
        row = by[r].get(77)
        if not row:
            w("  %-28s 面板 78 无报文" % short(r, a.viewer))
            continue
        nb += bool(row["n_cells"])
        nh += bool(row["hand"])
        w("  %-28s 板 %d 格(稽古 %d 普通 %d) 有手 %d cmd %s Lv 前 %s 只剩稽古 %d camp_left %s 比赛 %d CP 数 %d"
          % (short(r, a.viewer), row["n_cells"], row["n_keiko"], row["n_plain"], row["hand"],
             row["cmd"], row["act_lv_before"], row["keiko_only"], row["camp_left"], row["race"],
             row["n_cp_end"]))
    w("  ⇒ 引擎 77 有训练手 %d/%d 局；有预告板 %d/%d 局" % (nh, len(runs), nb, len(runs)))
    w("")

    w("== 7. P1 同局同格（稽古格 5101–5105）体力预告分段范围（home+festa 合并）==")
    seg = (("合宿60–63", range(60, 64)), ("64–66", range(64, 67)), ("本番67", (67,)),
           ("争议68–71", range(68, 72)), ("收尾72–77", range(72, 78)))
    for r in runs:
        w("  %-28s%s" % (short(r, a.viewer), "".join("%-16s" % ("格%d" % c) for c in sorted(KEIKO))))
        for lab, ts in seg:
            cells = collections.defaultdict(list)
            for et in ts:
                row = by[r].get(et)
                if not row:
                    continue
                for cid, v in parse_pred(row["pred_pv"]):
                    if cid in KEIKO:
                        cells[cid].append((et, v))
            w("  %-28s%s" % ("  " + lab, "".join("%-16s" % rng(cells.get(c)) for c in sorted(KEIKO))))
    w("")
    w("  同格台阶（相邻两次出现变化 ≥%d 才列；显示过滤，不是常数）：" % STEP_MIN)
    for r in runs:
        steps = []
        for cid in sorted(KEIKO):
            ser = []
            for et in sorted(by[r]):
                if et < 55:
                    continue
                for c, v in parse_pred(by[r][et]["pred_pv"]):
                    if c == cid:
                        ser.append((et, v))
            for i in range(1, len(ser)):
                if abs(ser[i][1] - ser[i - 1][1]) >= STEP_MIN:
                    steps.append("格%d %+d(%d→%d)" % (cid, ser[i][1] - ser[i - 1][1],
                                                      ser[i - 1][0], ser[i][0]))
        w("  %-28s %s" % (short(r, a.viewer), "; ".join(steps) or "无 ≥%d 台阶" % STEP_MIN))
    w("")

    w("== 8. 逐局 onset 与违例（onset = 该引擎回合起不再出现违例）==")
    w("  %-28s%s" % ("run", "".join("%-18s" % k for k in SIGS)))
    agg, viol = collections.defaultdict(collections.Counter), collections.defaultdict(list)
    for r in runs:
        line = "  %-28s" % short(r, a.viewer)
        for key in SIGS:
            ons, vio = onset(by, r, key, ctx)
            agg[key][ons] += 1
            viol[key] += vio
            line += "%-18s" % ("—" if ons is None else "%d%s" % (ons, ("(违%s)" % span(vio)) if vio else ""))
        w(line)
    for key in SIGS:
        w("    %-18s onset %s 违例 %s"
          % (key, dict(sorted(agg[key].items(), key=lambda x: (x[0] is None, x[0]))),
             span(viol[key]) or "无"))
    w("")

    w("== 9. 三态裁决（每条签名对三个候选窗各算一遍；计数单位 = (run, 引擎回合)）==")
    for key in SIGS:
        w("  -- %s%s --" % (key, "（审计位，不参裁）" if key not in ADJ else ""))
        for lab, win in CAND:
            sup, con, na, exc = tally(runs, by, key, ctx, win)
            if key not in ADJ:
                v = "只登记，不裁"
            elif not sup and not con:
                v = "不能判"
            elif con and not sup:
                v = "反对（窗内只有违例）"
            elif con == 0:
                v = "支持（0 违例）"
            else:
                v = "倾向支持（例外 %d 条 ⇒ 有例外就不能当硬边界）" % con
            w("    %-18s 支持 %3d 违例 %3d 判不了 %3d ⇒ %s%s"
              % (lab, sup, con, na, v, ("；例外 " + ", ".join(exc[:8])) if exc else ""))
    w("")

    w("== 10. 遮蔽与判不了 ==")
    w("  干劲实测值域 %s（上限 %d）⇒ 板前已满档的手 +1 被吃掉，Q3a 只能证伪"
      % (sorted({row["mot_end"] for row in rows if row["mot_end"] is not None}), mot_cap))
    for r in runs:
        d = by[r]
        sat = [et for et in d if et >= 64 and d[et]["max_vital"] and d[et]["vital_end"] is not None
               and d[et]["vital_end"] >= d[et]["max_vital"]]
        m5 = [et for et in d if et >= 64 and d[et]["hand"] and d[et]["mot_at_board"] == mot_cap]
        cap = [et for et in d if et >= 64 and d[et]["lv_capped"]]
        w("  %-28s 引擎64+：体力=上限 %s｜板前干劲满 %s｜演目 Lv 触顶 %s｜Q1a %s"
          % (short(r, a.viewer), span(sat), span(m5), span(cap),
             "无参照(该局没报过 fr>0)" if ctx["fr_ref"][r] is None else "参照体力≤%s" % ctx["fr_ref"][r]))
    w("  注：`result_state` 非 2 的样本极少（值域见 §3），Q1b 只当反证；本轮结论不单独依赖它。")
    w("")

    # ---- §11 窗左违例双证（面板 68–72 = 引擎 67–71）
    on = 72
    w("== 11. 面板 68–72（引擎 67–71）逐 (run,turn) 双证：板上有普通格 + 当回合实际取了什么手 ==")
    w("  %-28s %-5s %-5s %-6s %-6s %-6s %-8s %-8s %-8s %s"
      % ("run", "面板", "引擎", "格数", "普通格", "稽古格", "只剩稽古", "收尾形状", "实际手", "fr_max"))
    n_cells_plain = n_used_plain = 0
    for r in runs:
        for et in range(on - 5, on):
            row = by[r].get(et)
            if not row:
                continue
            if row["n_plain"] > 0:
                n_cells_plain += 1
            if row["cmd"] in PLAIN:
                n_used_plain += 1
            w("  %-28s %-5s %-5s %-6s %-6s %-6s %-8s %-8s %-8s %s%%"
              % (short(r, a.viewer), et + 1, et, row["n_cells"], row["n_plain"], row["n_keiko"],
                 row["keiko_only"], row["finale_shape"],
                 "%s%s" % (row["cmd"] or "—", "" if row["hand"] else "(没手)"), row["fr_max"]))
    w("  ⇒ 45 格（9 局 × 面板 68–72）里板上有普通训练格 %d 格；当回合**实际取普通手** %d 格 "
      "（cmd ∈ %s）⇒ 「收尾期形状没起来」不是判不了，是真有普通训练并被使用"
      % (n_cells_plain, n_used_plain, sorted(PLAIN)))
    w("")

    # ---- §12 体力 +30 这一腿：同格预告 窗口内 vs 窗口外
    w("== 12. S1 体力+30 单判：同一稽古格的体力预告分布（合并 home+festa），窗口内外对比 ==")
    seg_defs = [("合宿 面板61–64", lambda p: 61 <= p <= 64),
                ("窗外 面板65–72", lambda p: 65 <= p <= 72),
                ("窗内 面板73–78", lambda p: 73 <= p <= 78)]
    w("  %-8s" % "格" + "".join("  %-26s" % lb for lb, _ in seg_defs) + "段中位差(内−窗外)")
    for cid in sorted(KEIKO) + ["合并"]:
        med = {}
        line = "  %-8s" % cid
        for lb, sel in seg_defs:
            vs = sorted(v for row in rows if sel(row["panel_turn"])
                        for c, v in parse_pred(row["pred_pv"])
                        if (c in KEIKO if cid == "合并" else c == cid))
            med[lb] = vs[len(vs) // 2] if vs else None
            line += "  n=%-4d %s..%s 中%+5s" % (
                len(vs), min(vs) if vs else "–", max(vs) if vs else "–",
                med[lb] if med[lb] is not None else "–")
        a_, b_ = med["窗外 面板65–72"], med["窗内 面板73–78"]
        w(line + "  " + (("%+d" % (b_ - a_)) if None not in (a_, b_) else "判不了"))
    w("  主线点名的口径（窗外 = 面板 65–71，窗内 = 有手的 73/75/77；另列 race 回合 74/76/78 当对照）：")
    seg2 = [("窗外 65–71", lambda p: 65 <= p <= 71),
            ("窗内 73/75/77", lambda p: p in (73, 75, 77)),
            ("race 74/76/78", lambda p: p in (74, 76, 78))]
    w("  %-8s" % "格" + "".join("  %-26s" % lb for lb, _ in seg2) + "中位差(窗内−窗外)")
    for cid in sorted(KEIKO) + ["合并"]:
        med2 = {}
        line = "  %-8s" % cid
        for lb, sel in seg2:
            vs = sorted(v for row in rows if sel(row["panel_turn"])
                        for c, v in parse_pred(row["pred_pv"])
                        if (c in KEIKO if cid == "合并" else c == cid))
            med2[lb] = vs[len(vs) // 2] if vs else None
            line += "  n=%-4d %s..%s 中%+5s" % (
                len(vs), min(vs) if vs else "–", max(vs) if vs else "–",
                med2[lb] if med2[lb] is not None else "–")
        a2, b2 = med2["窗外 65–71"], med2["窗内 73/75/77"]
        w(line + "  " + (("%+d" % (b2 - a2)) if None not in (a2, b2) else "判不了"))
    w("  （逐回合同格值在 CSV 的 `pred_pv` / `pred_pv_home` / `pred_pv_festa_hand`）")
    w("  对照：单手结算残差（Δ体力 − 合并预告）在窗内 73/75/77 的实际手里：")
    for r in runs:
        det = []
        for et in range(on, 78):
            row = by[r].get(et)
            if row and row["hand"]:
                det.append("面板%d cmd%s 预告%s Δ%s 残差%s%s"
                           % (et + 1, row["cmd"], row["pred_pv_hand"], row["d_vital_hand"],
                              row["resid_vital"],
                              "(顶上限)" if row["max_vital"] and row["vital_end"] is not None
                              and row["vital_end"] >= row["max_vital"] else ""))
        w("    %-28s %s" % (short(r, a.viewer), "; ".join(det) or "窗内无结算手"))
    ok = lambda row: row["hand"] and row["r_state"] == 2
    n_res0 = sum(1 for row in rows if ok(row) and row["resid_vital"] is not None
                 and abs(row["resid_vital"]) <= 3)
    n_res30 = sum(1 for row in rows if ok(row) and row["resid_vital"] is not None
                  and abs(row["resid_vital"] - 30) <= 8)
    n_fail_refund = sum(1 for row in rows if row["hand"] and row["r_state"] not in (2,)
                        and row["resid_vital"] is not None and abs(row["resid_vital"] - 30) <= 8)
    w("  ⇒ **成功手**（`result_state=2`）的可判残差：≈0 的 %d 手，≈+30 的 %d 手；"
      "另有 %d 手残差 ≈+30 是**失败没扣体力**（`result_state≠2`，全在窗外）⇒ 不是 perk"
      % (n_res0, n_res30, n_fail_refund))
    w("  ⇒ 结算层查不到 +30；若 +30 存在只能住在**预告层**，但段中位差是 5101..5104 = +5..+13、"
      "只有 5105 = +40（合并 +9；主线口径下 +8/+13/+9/+9/+40，合并 +11）"
      "⇒ 既不是整体 +30 的位移，也不是一个常数 ⇒ 作为「每手 +30」常数：**否证**")
    n_in0 = sum(1 for row in rows if ok(row) and row["panel_turn"] >= 73
                and row["resid_vital"] is not None and abs(row["resid_vital"]) <= 3)
    n_in_all = sum(1 for row in rows if ok(row) and row["panel_turn"] >= 73
                   and row["resid_vital"] is not None)
    w("  ⇒ 窗内（面板 ≥73）可判残差 %d 手中 ≈0 的 %d 手；其余是体力顶上限被夹（逐局明细见上）"
      % (n_in_all, n_in0))
    w("")

    # ---- §13 演目 Lv+1：窗口内外稽古手对照
    w("== 13. Lv+1 是不是收尾期专属：稽古手（cmd 510x）按面板 73 边界拆两段 ==")
    w("  %-30s %-6s %-6s %-8s %-8s %-8s %-8s" %
      ("段", "手数", "ΔLv>0", "其中不花槽(perk)", "其中花槽", "ΔLv=0", "Lv触顶判不了"))
    for lb, sel in (("窗外 面板 ≤72", lambda p: p <= 72), ("窗内 面板 ≥73", lambda p: p >= 73)):
        sub = [row for row in rows if row["hand"] and row["cmd"] in KEIKO and sel(row["panel_turn"])]
        up = [row for row in sub if row["d_lv"] and row["d_lv"] > 0]
        perk = [row for row in up if row["perk_lv"] == 1]
        drain = [row for row in up if row["perk_lv"] == 0]
        flat = [row for row in sub if row["d_lv"] == 0]
        cap = [row for row in sub if row["lv_capped"]]
        w("  %-30s %-6d %-6d %-8d %-8d %-8d %-8d" %
          (lb, len(sub), len(up), len(perk), len(drain), len(flat), len(cap)))
    w("  主线点名的口径（只取面板 65–71 的稽古手，与窗内 73–78 直接对照）：")
    for lb, sel in (("窗外 面板 65–71", lambda p: 65 <= p <= 71),
                    ("窗内 面板 73–78", lambda p: p >= 73)):
        sub = [row for row in rows if row["hand"] and row["cmd"] in KEIKO and sel(row["panel_turn"])]
        up = [row for row in sub if row["d_lv"] and row["d_lv"] > 0]
        perk = [row for row in up if row["perk_lv"] == 1]
        flat = [row for row in sub if row["d_lv"] == 0]
        cap = [row for row in sub if row["lv_capped"]]
        w("    %-18s 手数 %-4d ΔLv>0 %-4d 其中不花槽 %-4d 花槽 %-4d ΔLv=0 %-4d 触顶 %-4d"
          % (lb, len(sub), len(up), len(perk), len(up) - len(perk), len(flat), len(cap)))
    w("  逐手明细（窗内每手都列；窗外列稽古手）：")
    plain_in = [row for row in rows if row["hand"] and row["cmd"] in PLAIN and row["panel_turn"] >= 73]
    keiko_in = [row for row in rows if row["hand"] and row["cmd"] in KEIKO and row["panel_turn"] >= 73]
    plain_out = [row for row in rows if row["hand"] and row["cmd"] in PLAIN and 65 <= row["panel_turn"] <= 72]
    w("  ⇒ 窗内（面板 73–78）**结算手全是稽古**：稽古 %d 手 / 普通训练 %d 手；"
      "窗外（面板 65–72）普通手 %d 手 ⇒ 收尾期 perk 若挂在**普通训练**腿上一格都不会触发"
      % (len(keiko_in), len(plain_in), len(plain_out)))
    n_lit = sum(1 for row in keiko_in
                if row["gauge_before"] is not None and row["gauge_before"] >= GAUGE_MAX
                and row["gauge_after"] is not None and row["gauge_after"] >= GAUGE_MAX
                and row["lv_before"] is not None and row["lv_before"] < 10)
    w("  ⇒ 接线核对：窗内稽古手 %d 手里「前后都满槽(≥%d) 且升级前 Lv<10」= 引擎 `lit` 条件的有 %d 手"
      "（`traits.rs:92-95`）⇒ 同一决策在引擎里走 `Action::Keiko` → `do_keiko`（`mod.rs:1290`）→ "
      "`do_keiko_impl(level_up=true)`，而 `67..=76` 只出现在 `do_train` 里（`:672` 失败率归零、`:801` perk）"
      "⇒ perk 与 Lv+1/干劲+1/体力+30 都触发不了，且 `:1485` 会把槽清成 0（真机窗内是 5→5）"
      % (len(keiko_in), GAUGE_MAX, n_lit))
    for row in sorted((x for x in rows if x["hand"] and x["cmd"] in KEIKO),
                      key=lambda x: (x["run"], x["panel_turn"])):
        if row["panel_turn"] < 55:
            continue
        w("    %-28s 面板%-4s 引擎%-4s cmd%-5s 格%-2s Lv %s→%s 槽 %s→%s need %s ⇒ %s"
          % (short(row["run"], a.viewer), row["panel_turn"], row["eng_turn"], row["cmd"],
             row["act_of_cmd"], row["lv_before"], row["lv_after"], row["gauge_before"],
             row["gauge_after"], row["need_gauge"],
             "perk(+1 不花槽)" if row["perk_lv"] == 1 else
             ("触顶判不了" if row["lv_capped"] else
              ("花槽正常升级" if row["d_lv"] and row["d_lv"] > 0 else "没升"))))
    w("")

    txt = "\n".join(L)
    os.makedirs(os.path.dirname(OUT_TXT), exist_ok=True)
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        cw = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        cw.writeheader()
        cw.writerows(rows)
    print(txt)
    print("\n[写出] %s（%d 行）\n[写出] %s" % (OUT_CSV, len(rows), OUT_TXT))


if __name__ == "__main__":
    main()
