#!/usr/bin/env python3
"""演目「槽 / Lv」赠送落点报文核对（#58 纯报文＋表侧数据线）。

一条命令重建全部表：

    python tools/outing_act_level_scan.py
    python tools/outing_act_level_scan.py --viewer 238107205 --scen 15
    python tools/outing_act_level_scan.py --quiet

**回合索引口径**（报文自证，不靠记忆）：
  报文 `chara_info.turn` = 面板显示值 = master.mdb `single_mode_turn.turn` = **1 基（1–78）**
  引擎 `base.turn` / memo 的 `turn N` = **0 基** ⇒ 引擎回合 = 报文回合 − 1
  本报告一律同时写两轴：列名 `t面板` / `t引擎`。

判据优先级（主线 11-04 硬要求）：
  **Q-A 主判据 = 预告字段本身** `festa_data_set.command_gain_act_gauge_array`
     = [{command_type, predict_gain_act_gauge_array:[{act_type, gain_act_gauge}]}]
     服务端已经替我们把「这一格送到哪儿」算好了；前后差分只当交叉验证（次判据）。
  **Q-B 主判据 = 直读字段** `festa_data_set.act_info_array[].friend_outing_gain_act_level`
     （= 反汇编里的 `ActTypeLevelUpByFriendOutingDict` 的运行时投影）。
     表侧锚 = `single_mode_15_outing_effect(support_card_id, story_step, add_level)`。

红线：不改 Rust、不写 `data/`、不跑引擎、不做 git。只读 `master.mdb`（`mode=ro`）。
产物：`out/_outing_act_level_turns.csv`（逐 run×面板回合全量）/ `out/_outing_act_level_report.txt`。
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
MASTER = r"E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
OUT_CSV = os.path.join(ROOT, "out", "_outing_act_level_turns.csv")
OUT_TXT = os.path.join(ROOT, "out", "_outing_act_level_report.txt")

# 演目槽上限：`crates/matsuri-core/src/game/mod.rs:33 ACT_GAUGE_MAX = 5`；
# 报文自证 `need_act_gauge` 恒 = 5（见报告 §1），只用来判引擎的 `lit`，不是本文产出的常数。
GAUGE_MAX = 5
ACT_TYPES = (1, 2, 3, 4, 5)
# 报文 act_type → 引擎 0 基下标（引擎 `act_gauge[i]` / `act_level[i]`）
ENG_IDX = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
# memo §0.4 / U07 的三级 tie-break 用的属性顺序；报文只有 act_type，没有属性名，
# 故 ③ 用 act_type 升序代表（`single_mode_15_act_info` 自证 act_type↔5101..5105 线性）。
GA_TYPES = (3, 4, 7, 8)
# 主判据模型的平票破法（本库 k=4 全命中那一种，见报告 §6 模型对照）
TIE_MAIN = "orig"
# 决胜期起点：面板 73（= 引擎 72）。报文自证：面板 >=73 的 exec_command 只有
# 训练（73/75/77 各 9 手）与比赛（74/76/78 各 9 场），**从未出现外出/休息/入院**，
# 且四源预告恒 0 ⇒ 这些行不能拿来裁「落点规则」，只能记「该区间这些源不再送槽」。
FINALE_PANEL_FROM = 73
GA_NAME = {3: "外出系(301/304/390)", 4: "比赛", 7: "休息(701)", 8: "保健室/入院(801)"}
SRC_OF_CT = {3: "外出", 4: "比赛", 7: "休息", 8: "入院"}
# 训练格（用来排除：训练送的槽走 command_info_array，不在本四源里）
PLAIN = {101, 102, 103, 104, 105, 106}
KEIKO = {5101, 5102, 5103, 5104, 5105}


# ---------------------------------------------------------------- 报文抽取

def acts_of(fds):
    """act_type → (Lv, 槽, 友人外出指针 F)。F = `friend_outing_gain_act_level`。"""
    out = {}
    for a in fds.get("act_info_array") or []:
        if isinstance(a, dict) and a.get("act_type") is not None:
            out[int(a["act_type"])] = (int(a.get("level") or 0), int(a.get("act_gauge") or 0),
                                       int(a.get("friend_outing_gain_act_level") or 0),
                                       int(a.get("act_training_state") or 0))
    return out


def ga_of(fds):
    """command_type → {act_type: 预告增量}；数组缺失返回 None（区别于「存在但为空」）。"""
    ga = fds.get("command_gain_act_gauge_array")
    if not isinstance(ga, list):
        return None
    out = {}
    for e in ga:
        if not isinstance(e, dict):
            continue
        ct = e.get("command_type")
        d = {}
        for x in e.get("predict_gain_act_gauge_array") or []:
            if isinstance(x, dict) and x.get("act_type") is not None:
                d[int(x["act_type"])] = d.get(int(x["act_type"]), 0) + int(x.get("gain_act_gauge") or 0)
        if ct is not None:
            out[int(ct)] = d
    return out


def cell_ga_of(fds):
    """训练格预告 command_id → {act_type: 增量}（本脚本只用来证明「训练源不在这四格里」）。"""
    out = {}
    for c in fds.get("command_info_array") or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("command_id")
        d = {}
        for x in c.get("predict_gain_act_gauge_array") or []:
            if isinstance(x, dict) and x.get("act_type") is not None:
                d[int(x["act_type"])] = int(x.get("gain_act_gauge") or 0)
        if cid is not None:
            out[int(cid)] = d
    return out


def read_chara_info(data):
    ci = data.get("chara_info")
    if not isinstance(ci, dict):
        lc = data.get("single_mode_load_common")
        if isinstance(lc, dict):
            ci = lc.get("chara_info")
    return ci if isinstance(ci, dict) else None


def load_stream(viewer, scen, lo, hi, verbose=True):
    """按文件名时序返回 (resp, req) 交错流；resp 带 run / 面板回合 / 演目板 / 预告四源。"""
    resp = []
    req = []
    names = sorted(os.listdir(RAW))
    ndec = nskip = 0
    for name in names:
        path = os.path.join(RAW, name)
        if not os.path.isfile(path):
            continue
        try:
            d = decode_file(path)
        except Exception:
            nskip += 1
            continue
        if not isinstance(d, dict):
            nskip += 1
            continue
        ndec += 1
        is_req = name.endswith("notify_request.bin")
        if is_req:
            payload = None
            for k in sorted(d.keys()):
                if k.endswith("_request_common") and isinstance(d[k], dict):
                    payload = (k, d[k])
                    break
            if payload is None:
                continue
            key, body = payload
            v = d.get("viewer_id") or (d.get("data_headers") or {}).get("viewer_id")
            if viewer and v != viewer:
                continue
            req.append({"file": name, "key": key.replace("single_mode_", "").replace("_request_common", ""),
                        "viewer": v, "turn": body.get("current_turn"), "body": body,
                        "ct": body.get("command_type"), "cid": body.get("command_id"),
                        "grp": body.get("command_group_id"), "sel": body.get("select_id"),
                        "vital": body.get("current_vital")})
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = read_chara_info(data)
        if not isinstance(ci, dict) or ci.get("scenario_id") != scen or ci.get("turn") is None:
            continue
        v = d.get("viewer_id") or (d.get("data_headers") or {}).get("viewer_id")
        if viewer and v != viewer:
            continue
        t = ci.get("turn")
        if t is None or not (lo <= t <= hi):
            continue
        fds = data.get("festa_data_set")
        if not isinstance(fds, dict):
            fds = {}
        fi = fds.get("festa_info") or {}
        cr = data.get("command_result")
        cr = cr if isinstance(cr, dict) else {}
        resp.append({
            "file": name, "viewer": v, "turn": t,
            "run": "%s@%s@%s" % (v, ci.get("single_mode_chara_id"), ci.get("start_time")),
            "chara": ci.get("single_mode_chara_id"), "start": ci.get("start_time"),
            "vital": ci.get("vital"), "max_vital": ci.get("max_vital"), "mot": ci.get("motivation"),
            "five": [ci.get(k) or 0 for k in ("speed", "stamina", "power", "guts", "wiz")],
            "acts": acts_of(fds), "ga": ga_of(fds), "cell_ga": cell_ga_of(fds),
            "camp_left": fi.get("summer_camp_remain_turn_num"), "max_act": fi.get("max_act_level"),
            "cr_cid": cr.get("command_id"), "cr_state": cr.get("result_state"),
            "is_load": ("single_mode_load_common" in data),
            "n_eval": len(fds.get("evaluation_info_array") or []),
        })
    if verbose:
        sys.stderr.write("解码 %d 个报文（跳过 %d）；scen=%s 带 chara_info 的 resp %d / req %d\n"
                         % (ndec, nskip, scen, len(resp), len(req)))
    return resp, req


def index_by_run(resp, req):
    """run → 面板回合 → {resps, reqs, board}；board 取该回合第一条带四源预告的报文。"""
    runs = collections.defaultdict(lambda: collections.defaultdict(lambda: {"resps": [], "reqs": []}))
    # req 归到「下一次 resp 之前的最近一个 req」：报文里 req 与 resp 严格交错 ⇒
    # 用文件名序号排序后线性扫。
    allrows = sorted(resp + req, key=lambda r: r["file"])
    cur_run = None
    for r in allrows:
        if "run" in r:
            cur_run = r["run"]
        if cur_run is None:
            continue
        t = r.get("turn")
        if t is None:
            continue
        slot = runs[cur_run][t]
        if "key" in r:
            slot["reqs"].append(r)
        else:
            slot["resps"].append(r)
            if r["ga"] is not None and slot.get("board") is None:
                slot["board"] = r
    for run, turns in runs.items():
        for t, slot in turns.items():
            rs = slot["resps"]
            slot["last"] = rs[-1] if rs else None
            # 下一回合第一条带 acts 的报文 = 本回合动作的落地态
            nxt = runs[run].get(t + 1)
            landing = None
            if nxt:
                for r in nxt["resps"]:
                    if r["acts"]:
                        landing = r
                        break
            slot["landing"] = landing or (nxt["resps"][0] if nxt and nxt["resps"] else None)
    return runs


# ---------------------------------------------------------------- 落点模型

def dist(acts, k, tie="orig"):
    """把 k 点槽**逐点**派发 k 次；每次取 argmin，满槽（槽 >= need_act_gauge = 5）不入。

    argmin 的键（主判据模型，本库 k=4 全部 76/76 命中）：
        (Lv, 当前槽, 批次开始时的槽, act_type)
    —— 前两级就是 U07 的「① Lv 最低 → ② 槽最低」；第③级「仍相同」在报文里
       自证为「批次开始时槽更低者优先」（= 上一轮没被填满的那格继续优先），
       第④级才是 act_type 升序（近似「速/耐/…」的属性序，见 §8-①）。

    tie 参数是三种平票破法的对照开关，只用于 §6 的模型对照表：
        "orig"  = 批次开始时的槽（主模型）
        "flag"  = 友人外出指针优先
        "index" = 只看 act_type 升序（= 现引擎 push_act_gauge 的形状）
    """
    lv = {a: acts[a][0] for a in acts}
    base = {a: acts[a][1] for a in acts}
    fl = {a: acts[a][2] for a in acts}
    g = dict(base)
    out = collections.Counter()
    for _ in range(k):
        cand = [a for a in ACT_TYPES if a in g and g[a] < GAUGE_MAX]
        if not cand:
            break

        def keyf(a):
            if tie == "orig":
                third = base[a]
            elif tie == "flag":
                third = 0 if fl[a] else 1
            else:
                third = 0
            return (lv[a], g[a], third, a)

        best = min(cand, key=keyf)
        g[best] += 1
        out[best] += 1
    return dict(out)


def shape(d):
    """预告/模型 dict → 人话形状串，如 '3+1' / '1+1+1+1'。"""
    return "+".join(str(v) for _, v in sorted(d.items(), key=lambda x: -x[1])) if d else "0"


# ---------------------------------------------------------------- 表侧锚

def mdb_anchors():
    out = {"outing": [], "outing_set": [], "outing_effect": [], "act_info": [], "need": "mdb 读不到"}
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
        cur = con.cursor()
        cur.execute("select id, command_group_id, condition, is_play_cutt from single_mode_outing "
                    "order by command_group_id")
        out["outing"] = cur.fetchall()
        cur.execute("select id, command_group_id_1, command_group_id_2 from single_mode_outing_set "
                    "order by id")
        out["outing_set"] = [(r[0], r[1], r[2]) for r in cur.fetchall()]
        cur.execute("select support_card_id, count(*), group_concat(story_step), group_concat(add_level) "
                    "from single_mode_15_outing_effect group by support_card_id order by support_card_id")
        out["outing_effect"] = cur.fetchall()
        cur.execute("select act_type, command_id from single_mode_15_act_info order by act_type")
        out["act_info"] = cur.fetchall()
        cur.execute("select min(need_act_gauge), max(need_act_gauge), count(*) from single_mode_15_act_effect")
        out["need"] = "act_effect 无 need_act_gauge 列（该表是 Lv 效果表）"
        con.close()
    except Exception as exc:  # 只读连接失败要如实写出来
        out["need"] = "mdb 读失败: %s" % exc
    return out


# ---------------------------------------------------------------- 主裁

def adjudicate(runs, args):
    rows = []
    tally = collections.defaultdict(lambda: collections.Counter())
    ga_shape = collections.defaultdict(collections.Counter)
    k4_cases = []
    qb_cases = []
    confound = []

    for run in sorted(runs):
        turns = runs[run]
        for t in sorted(turns):
            slot = turns[t]
            board = slot.get("board")
            last = slot.get("last")
            src = board or last
            if src is None:
                continue
            acts = src["acts"]
            if not acts:
                continue
            et = t - 1
            finale = t >= FINALE_PANEL_FROM
            state2 = all(acts[a][3] == 2 for a in acts)
            row = {"run": run, "viewer": src["viewer"], "chara": src["chara"], "start": src["start"],
                   "t面板": t, "t引擎": et, "board_file": src["file"],
                   "板_Lv": "|".join("%d:%d" % (a, acts[a][0]) for a in ACT_TYPES if a in acts),
                   "板_槽": "|".join("%d:%d" % (a, acts[a][1]) for a in ACT_TYPES if a in acts),
                   "板_F": "|".join("%d:%d" % (a, acts[a][2]) for a in ACT_TYPES if a in acts),
                   "板_状态": "|".join("%d:%d" % (a, acts[a][3]) for a in ACT_TYPES if a in acts),
                   "决胜期": 1 if finale else 0, "演目锁定": 1 if state2 else 0,
                   "F_标记": next((a for a in ACT_TYPES if a in acts and acts[a][2] == 1), 0),
                   "camp剩余": src["camp_left"], "max_act": src["max_act"],
                   "vital": src["vital"], "干劲": src["mot"]}

            # ---- Q-A 主判据：逐源比对预告四源 vs 模型
            ga = src["ga"]
            for ct in GA_TYPES:
                real = None if ga is None else ga.get(ct)
                k = 0 if real is None else sum(real.values())
                mdl = dist(acts, k, tie=TIE_MAIN) if real is not None else None
                min_lv = min(acts[a][0] for a in acts)
                tier_full = all(acts[a][1] >= GAUGE_MAX for a in acts if acts[a][0] == min_lv)
                if real is None:
                    v = "判不了"
                    why = "该回合报文里没有 command_gain_act_gauge_array（事件/动画链中间包）"
                elif finale and real == {}:
                    v = "判不了"
                    why = ("决胜期(面板 >=%d) 该源预告 0：本库该区间从未出现这一手的正样本，"
                          "分不开「该源不可选」与「该源不送槽」" % FINALE_PANEL_FROM)
                elif ct == 8:
                    v = "支持" if real == {} else "违例"
                    why = "入院恒 0 点（799/799 报文空数组）" if real == {} else "入院出现了槽"
                elif real == {} and tier_full:
                    v = "支持"
                    why = "最低 Lv 档全满 ⇒ 预告 0（满槽不溢出）"
                elif real == {} and all(acts[a][1] == 0 and acts[a][0] == 1 for a in acts):
                    v = "判不了"
                    why = "全 0 板（single_mode_load_common 的未初始化副本）"
                elif real == {}:
                    v = "违例"
                    why = "有空槽却预告 0（最低 Lv 档没满）"
                else:
                    v = "支持" if mdl == real else "违例"
                    why = "主模型 %s / 预告 %s" % (shape(mdl), shape(real))
                    if ct == 4 and k == 4:
                        k4_cases.append({"run": run, "t面板": t, "t引擎": et, "file": src["file"],
                                         "acts": dict(acts), "real": dict(real), "mdl": dict(mdl),
                                         "shape_real": shape(real), "shape_mdl": shape(mdl), "v": v})
                tally["QA主判"][ (ct, v) ] += 1
                row["预告%d" % ct] = "|".join("%d+%d" % (a, b) for a, b in sorted(real.items())) if real else ""
                row["主判%d" % ct] = v
                row["主判%d说明" % ct] = why if v != "支持" else ""
                if real:
                    ga_shape[ct][shape(real)] += 1

            # ---- Q-A 次判据（只做旁证）：板 → 落地 的槽/级差分
            # 红线：抓包流里两条 run 交错（同一个 02:48:48 前缀下既有 run@171 的 _0001
            #      也有另一条 run 的 _2175），所以 exec_command 的请求标签并不可靠
            #      （实测把 3 个友人外出标成了「比赛」）。本表改用**差分签名**自证动作，
            #      请求标签只当旁注列（`本回合动作`），不进判据。
            # 比对的「板」取本回合**最后一个带预告四源的帧**（离落地最近的那次预告）。
            reqs = [r for r in slot["reqs"] if r["key"] == "exec_command"]
            race = [r for r in slot["reqs"] if r["key"] in ("race_start", "race_entry", "race_out", "race_end")]
            act_kind = None
            if reqs:
                r0 = reqs[0]
                act_kind = {1: "训练", 3: "外出", 7: "休息", 8: "入院"}.get(r0["ct"], "ct%s" % r0["ct"])
                if r0["ct"] == 3:
                    act_kind = "外出grp%s" % r0["grp"]
            elif race:
                act_kind = "比赛"
            row["本回合动作"] = act_kind or ("无手" if not slot["reqs"] else slot["reqs"][0]["key"])

            gframes = [r for r in slot["resps"] if r["ga"] is not None and r["acts"]]
            gsrc = gframes[-1] if gframes else None
            land = slot.get("landing")
            sig = ""
            if gsrc is not None and land is not None and land["acts"]:
                a0, a1 = gsrc["acts"], land["acts"]
                dg = {a: a1[a][1] - a0[a][1] for a in a1 if a in a0}
                dl = {a: a1[a][0] - a0[a][0] for a in a1 if a in a0}
                got = dict((a, v) for a, v in dg.items() if v > 0)
                ups = dict((a, v) for a, v in dl.items() if v > 0)
                tot = sum(got.values())
                marked = next((a for a in ACT_TYPES if a in a0 and a0[a][2] == 1), None)
                downs = sorted([a for a, v in dg.items() if v < 0] + [a for a, v in dl.items() if v < 0])
                # 板之后、落地之前还夹着的帧：只允许「F 格 +1 级」与「实落格 +1 槽」这两件事
                gi = slot["resps"].index(gsrc)
                other_mid = False
                for f in slot["resps"][gi + 1:]:
                    fa = f["acts"]
                    if not fa:
                        continue
                    for a in ACT_TYPES:
                        if a not in fa or a not in a0:
                            continue
                        dlv = fa[a][0] - a0[a][0]
                        dgv = fa[a][1] - a0[a][1]
                        if dlv > (1 if a == marked else 0) or dgv > 1 or dgv < 0 or dlv < 0:
                            other_mid = True
                            break
                    if other_mid:
                        break
                # 差分签名
                friend = (len(ups) == 1 and list(ups.values())[0] == 1 and marked is not None
                          and list(ups)[0] == marked and tot == 1)
                if downs:
                    sig = "友人外出+负差" if friend else "负差"
                elif friend:
                    sig = "友人外出"
                elif tot == 0 and not ups:
                    sig = "无变化"
                elif tot == 0:
                    sig = "纯Lv+%d" % sum(ups.values())
                else:
                    sig = "纯槽+%d" % tot
                if downs:
                    v, why = "判不了", "落地前有负向差分（act%s：训练结算把槽花掉/等级被别的手动过），旁证被污染" % (
                        ",".join(str(x) for x in downs))
                    tally["QA次判污染"]["负向差分（训练结算）"] += 1
                elif friend:
                    post = dict((a, (a0[a][0] + (1 if a == marked else 0), a0[a][1], a0[a][2], a0[a][3]))
                                for a in a0)
                    mdl2 = dist(post, 1, tie=TIE_MAIN)
                    mdl1 = dist(a0, 1, tie=TIE_MAIN)
                    if got == mdl2:
                        v, why = "支持", "先给 F 格 +1 级，再按**更新后**的板 argmin +1 槽"
                        tally["QA次判时序"]["按新状态重算"] += 1
                    elif got == mdl1:
                        v, why = "支持", "沿用 +Lv 之前的旧板 argmin（没重算）"
                        tally["QA次判时序"]["沿用旧板"] += 1
                    else:
                        v, why = "违例", "新板模型 %s / 旧板模型 %s / 实落 %s" % (shape(mdl2), shape(mdl1), shape(got))
                    row["时序_新板"] = shape(mdl2) if got != mdl1 else ""
                elif other_mid:
                    v, why = "判不了", "板与落地之间还夹着别的状态推进帧（双流交错/断线重连），差分不同源"
                    tally["QA次判污染"]["板与落地之间夹了别的状态帧"] += 1
                elif tot == 0 and not ups:
                    pv = {c: (gsrc["ga"] or {}).get(c) for c in GA_TYPES}
                    if all(x == {} for x in pv.values()):
                        v, why = "支持", "四源预告皆 0，落地也确实 0"
                    else:
                        v, why = "判不了", "预告非 0 但落地 0 且签名不是友人外出（该手可能被事件/比赛链吃掉）"
                        tally["QA次判污染"]["预告非 0 却落地 0"] += 1
                elif tot in (1, 2, 3, 4):
                    mdl = dist(a0, tot, tie=TIE_MAIN)
                    v = "支持" if got == mdl else "违例"
                    why = "主模型 %s / 实落 %s" % (shape(mdl), shape(got))
                    if v == "违例":
                        row["违例帧"] = "%s→%s" % (gsrc["file"], land["file"])
                else:
                    v, why = "判不了", "落地差分 %d 点，超出 1..4 的模型范围" % tot
                    tally["QA次判污染"]["差分点数超出 1..4"] += 1
                tally["QA次判"][ (sig, v) ] += 1
                row["签名"] = sig
                row["次判说明"] = why
                row["次判"] = v
                row["次判实落"] = shape(got)
                row["次判落点"] = "|".join("%d+%d" % (a, b) for a, b in sorted(got.items()))
                row["次判预告"] = "|".join("ct%d:%s" % (c, shape((gsrc["ga"] or {}).get(c) or {})) for c in GA_TYPES)
                row["落地文件"] = land["file"]
                row["板帧"] = gsrc["file"]
                row["Lv动格"] = "|".join("%d+%d" % (a, b) for a, b in sorted(ups.items()))

                # ---- Q-B：这一手的 Lv 动了几格（按签名，不按请求标签）
                nl = len(ups)
                if nl == 5:
                    v2, why2 = "判不了", "五格齐 +1（合宿结束 confound），不归于外出"
                    confound.append({"run": run, "t面板": t, "file": land["file"], "act": sig})
                elif sig == "友人外出":
                    v2 = "支持" if nl == 1 and marked is not None and list(ups)[0] == marked else "违例"
                    why2 = "动了 %d 格 %s；F 指针 = act%s" % (
                        nl, "|".join("%d+%d" % (a, b) for a, b in sorted(ups.items())), marked)
                elif sig == "无变化":
                    v2, why2 = ("支持", "0 格 Lv 变化") if nl == 0 else ("违例", "0 槽却动了 %d 格 Lv" % nl)
                elif sig.startswith("纯槽"):
                    v2 = "支持" if nl == 0 else "违例"
                    why2 = "纯送槽的手却动了 %d 格 Lv：%s" % (nl, "|".join(
                        "%d+%d" % (a, b) for a, b in sorted(ups.items()))) if nl else ""
                else:
                    v2, why2 = "判不了", "签名 %s，Lv 归因不成立" % sig
                tally["QB落地"][ (sig, v2) ] += 1
                row["QB_动格数"] = nl
                row["QB_判"] = v2
                row["QB_说明"] = why2 if v2 != "支持" else ""
                if sig in ("友人外出",) or (sig.startswith("纯槽") and nl):
                    qb_cases.append({"run": run, "t面板": t, "t引擎": et, "act": sig, "dl": dl, "dg": dg,
                                     "marked": marked, "v": v2, "why": why2,
                                     "file": gsrc["file"], "land_file": land["file"]})
                # 普通外出（301/304）只能靠请求标签认，样本极稀；只在差分干净时入表，并标注小 N
                if act_kind in ("外出grp301", "外出grp304") and not downs and not other_mid:
                    vv = "支持" if nl == 0 else "违例"
                    qb_cases.append({"run": run, "t面板": t, "t引擎": et,
                                     "act": "普通外出%d格(%s)" % (nl, act_kind), "dl": dl, "dg": dg,
                                     "marked": marked, "v": vv,
                                     "why": "请求标签 %s，ΔLv=%s" % (act_kind, "|".join(
                                         "%d+%d" % (a, b) for a, b in sorted(ups.items())) or "无"),
                                     "file": gsrc["file"], "land_file": land["file"]})
                    tally["QB落地"][(("普通外出0格" if nl == 0 else "普通外出%d格" % nl), vv)] += 1
                if act_kind and sig == "友人外出" and not act_kind.startswith("外出"):
                    tally["标签不符"][(act_kind, sig)] += 1
                    row["标签不符"] = "%s/%s" % (act_kind, sig)

            elif land is not None and land["acts"]:
                tally["QA次判污染"]["本回合没有任何带预告四源的帧（事件/动画链中间包）"] += 1

            # ---- Q-B 主判据：F 指针本身是不是 argmin
            marked = row["F_标记"]
            if marked:
                arg = min([a for a in ACT_TYPES if a in acts], key=lambda a: (acts[a][0], acts[a][1], a))
                v = "支持" if arg == marked else "违例"
                why = "" if v == "支持" else "F=act%d，argmin(Lv,槽,type)=act%d" % (marked, arg)
            elif src["ga"] is not None:
                v = "判不了"
                why = "该局/该回合 F 指针全 0（无友人外出卡在场）"
            else:
                v = "判不了"
                why = "该回合没有预告板"
            tally["QB指针"][v] += 1
            row["QB_指针判"] = v
            row["QB_指针说明"] = why

            rows.append(row)
    return rows, tally, ga_shape, k4_cases, qb_cases, confound




# ---------------------------------------------------------------- 统计元信息

def dist_index(acts, k):
    """现引擎形状：argmin(Lv, 当前槽) 后按 act_type 升序。"""
    return dist(acts, k, tie="index")


def dist_rr(acts, k):
    """轮转假设：4 点必分给 4 个不同演目（各 1 点），按 argmin 顺序取前 k 格。"""
    cand = [a for a in ACT_TYPES if a in acts and acts[a][1] < GAUGE_MAX]
    order = sorted(cand, key=lambda a: (acts[a][0], acts[a][1], a))
    return {a: 1 for a in order[:k]}


def build_meta(resp, req, runs, rows, k4, qb, anchors):
    m = {}
    m["nresp"] = len(resp)
    m["nreq"] = len(req)
    m["turns"] = {r: len(runs[r]) for r in runs}
    m["boards"] = {r: sum(1 for t in runs[r] if runs[r][t].get("board")) for r in runs}

    exec_census = collections.Counter()
    grp_census = collections.Counter()
    for r in req:
        if r["key"] == "exec_command":
            exec_census[(r["ct"], r["cid"], r["grp"], r["sel"])] += 1
            if r["ct"] == 3:
                grp_census[r["grp"]] += 1
    m["exec_census"] = exec_census
    m["grp_census"] = grp_census

    gv = collections.defaultdict(collections.Counter)
    gl = collections.defaultdict(collections.Counter)
    for r in rows:
        act = r.get("本回合动作") or ""
        if act.startswith("外出grp"):
            gv[act.replace("外出grp", "")][(r.get("vital"), r.get("次判实落") or "-")] += 1
    for x in qb:
        gl[x["act"]][sum(1 for v in x["dl"].values() if v)] += 1
    m["grp_vital"] = {g: ", ".join("(vital=%s,槽=%s)×%d" % (k[0], k[1], v)
                                   for k, v in gv[g].most_common(3)) or "—" for g in grp_census}
    m["grp_lv"] = {g: ", ".join("%d格:%d手" % (k, v) for k, v in sorted(gl[g].items())) or "—"
                   for g in grp_census}
    m["lv_multi_rows"] = sum(1 for r in rows if r.get("Lv动格") and len(r["Lv动格"].split("|")) >= 2)
    m["lv_single_rows"] = sum(1 for r in rows if r.get("Lv动格") and len(r["Lv动格"].split("|")) == 1)
    m["n_friend_sig"] = sum(1 for x in qb if x["act"] == "友人外出")
    m["runs_with_390"] = len({x["run"] for x in qb if x["act"] == "友人外出"})

    m["need_n"] = sum(1 for r in resp if r["acts"])
    m["cell_ga_n"] = sum(1 for r in resp for c in r["cell_ga"].values() if c)

    fc = collections.Counter()
    f_zero = 0
    vs = [0, 0, 0]
    for r in resp:
        if not r["acts"]:
            continue
        n = sum(1 for a in r["acts"].values() if a[2] == 1)
        fc[n] += 1
        if n == 0:
            f_zero += 1
            continue
        marked = next(a for a in ACT_TYPES if a in r["acts"] and r["acts"][a][2] == 1)
        ga3 = (r["ga"] or {}).get(3)
        if not ga3:
            vs[2] += 1
        elif list(ga3)[0] == marked:
            vs[0] += 1
        else:
            vs[1] += 1
    m["f_census"] = dict(fc)
    m["f_zero"] = f_zero
    m["f_vs_ct3"] = vs

    models = []
    for name, fn, why in (
            ("批次开始时的槽破法（本报告主模型）",
             lambda a, k: dist(a, k, tie="orig"), "argmin(Lv, 当前槽, 批次开始时的槽, act_type)"),
            ("友人指针破法（F 优先）",
             lambda a, k: dist(a, k, tie="flag"), "argmin(Lv, 当前槽, 友人指针优先, act_type)"),
            ("act_type 破法（= 现引擎 push_act_gauge 的形状）",
             dist_index, "argmin(Lv, 当前槽) 再按 act_type 升序")):
        hit = sum(1 for x in k4 if fn(x["acts"], 4) == x["real"])
        models.append((name, hit, why))
    m["k4_models"] = models
    # 轮转假设：4 点必分到 4 个不同演目（各 1 点）
    m["k4_rr"] = sum(1 for x in k4 if dist_rr(x["acts"], 4) == x["real"])
    m["k4_rr_shape"] = sum(1 for x in k4 if len(x["real"]) == 4 and set(x["real"].values()) == {1})

    sens = [0, 0]
    for r in rows:
        for ct in (3, 7):
            val = r.get("预告%d" % ct)
            if not val:
                continue
            acts = {}
            for tok in r["板_Lv"].split("|"):
                a, v = tok.split(":")
                acts[int(a)] = [int(v), 0, 0, 0]
            for tok in r["板_槽"].split("|"):
                a, v = tok.split(":")
                acts[int(a)][1] = int(v)
            for tok in r["板_F"].split("|"):
                a, v = tok.split(":")
                acts[int(a)][2] = int(v)
            acts = {a: tuple(v) for a, v in acts.items()}
            real = {int(x.split("+")[0]): int(x.split("+")[1]) for x in val.split("|")}
            a1 = dist(acts, 1, tie=TIE_MAIN)
            a0 = dist(acts, 1, tie="index")
            if a1 == real:
                sens[0] += 1
            if a1 != a0:
                sens[1] += 1
    m["k1_flag_sensitivity"] = sens

    m["qb390_one"] = sum(1 for x in qb if x["act"] == "友人外出"
                         and sum(1 for v in x["dl"].values() if v) == 1)
    m["qb390_ge2"] = sum(1 for x in qb if x["act"] == "友人外出"
                         and sum(1 for v in x["dl"].values() if v) >= 2)
    m["qb_norm_zero"] = sum(1 for x in qb if x["act"].startswith("普通外出0格") and x["v"] == "支持")
    m["n390"] = sum(1 for x in qb if x["act"] == "友人外出")
    return m



# ---------------------------------------------------------------- 输出

def write_csv(rows, path):
    keys = ["run", "viewer", "chara", "start", "t面板", "t引擎", "board_file",
            "板_Lv", "板_槽", "板_F", "板_状态", "F_标记", "camp剩余", "max_act", "vital", "干劲",
            "决胜期", "演目锁定",
            "本回合动作", "签名", "板帧", "落地文件", "次判", "次判预告", "次判实落", "次判落点",
            "时序_新板", "Lv动格", "次判说明", "QB_动格数", "QB_判", "QB_说明", "标签不符", "违例帧",
            "QB_指针判", "QB_指针说明"]
    for ct in GA_TYPES:
        keys += ["预告%d" % ct, "主判%d" % ct, "主判%d说明" % ct]
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        wtr = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        wtr.writeheader()
        for r in sorted(rows, key=lambda x: (x["run"], x["t面板"])):
            wtr.writerow(r)


def render(args, rows, tally, ga_shape, k4, qb, confound, anchors, meta):
    o = []
    L = o.append
    g4 = ga_shape[4]
    rs = collections.Counter()
    for r in rows:
        for ct in GA_TYPES:
            if r.get("主判%d" % ct) == "判不了":
                rs[(ct, r["主判%d说明" % ct])] += 1
    L("演目「槽 / Lv」赠送落点 —— 纯报文＋表侧一手核对（#58 · 2026-11-04）")
    L("=" * 104)
    L("数据源：`%s`（只读 msgpack 抓包；按文件名 `HHMMSS_NNNN` 时序）" % RAW)
    L("表侧锚：`%s`（sqlite `mode=ro`）" % MASTER)
    L("逐 (run, 面板回合) 明细：`out/_outing_act_level_turns.csv` —— 本报告每个数字都能回查到具体某一帧")
    L("回合口径：报文 `chara_info.turn` = 面板值 = mdb `single_mode_turn.turn` = 1 基(1–78)；引擎回合 = 面板 − 1")
    L("")
    L("## 0. 样本规模")
    L("  scenario=%s 的 notify_response %d 个 / notify_request %d 个（viewer 过滤=%s，回合窗 %s–%s）"
      % (args.scen, meta["nresp"], meta["nreq"], args.viewer or "不限", args.lo, args.hi))
    L("  run 数 = %d" % len(meta["turns"]))
    for r in sorted(meta["turns"]):
        L("    %-36s 面板回合 %3d 个 / 带四源预告板 %3d 个" % (r, meta["turns"][r], meta["boards"][r]))
    L("  裁决行 %d 行；Q-A = 4 源 × %d 行 = %d 个裁决格" % (len(rows), len(rows), 4 * len(rows)))
    L("")
    L("## 1. 关键字段实测结构（command_gain_act_gauge_array 到底长什么样）")
    L("  路径：data.festa_data_set.command_gain_act_gauge_array（single_mode_load_common 里另带一份同样的）")
    L("  形状：**恒 4 项**，command_type 恒按 (3,4,7,8) 排列 —— 与只读档案")
    L("        _disasm_scen15/docs/SCENARIO-15-GAUGE.md「in-memory CommandGainActGaugeArray 恒 4 项、")
    L("        CommandType ∈ {3,4,7,8}」一致；该档案自列的「另一局是否也恒这样 [无证]」⇒ 本库全部局都恒 4 项。")
    L("  每项：{command_type:int, predict_gain_act_gauge_array:[{act_type:int, gain_act_gauge:int}]}")
    L("        增量**已按 act_type 合并** ⇒ 一项里最多 5 个元素；{act_type:4, gain_act_gauge:4}")
    L("        读作「4 点全给演目 4」，不是「4 个格各 1」。")
    L("  伴随：act_info_array[i] = {act_type, act_gauge, need_act_gauge, act_training_state, advice_id,")
    L("                              level, friend_outing_gain_act_level}")
    L("        带演目板的报文 %d 个；need_act_gauge 全库恒 = 5。" % meta["need_n"])
    L("  训练送的槽**不在这四格里**：训练格预告在 command_info_array[i].predict_gain_act_gauge_array")
    L("        （本库非空 %d 次）⇒ U07 只覆盖四源，训练侧不在本次裁决范围。" % meta["cell_ga_n"])
    L("")
    L("  四源 × 预告总点数分布（逐裁决行）：")
    L("    %-24s %s" % ("command_type", "总点数 → 行数"))
    for ct in GA_TYPES:
        cc = collections.Counter()
        for r in rows:
            val = r.get("预告%d" % ct) or ""
            s = sum(int(x.split("+")[1]) for x in val.split("|")) if val else 0
            cc[s] += 1
        L("    %-24s %s" % ("%d = %s" % (ct, GA_NAME[ct]),
                            ", ".join("%d点:%d行" % (k, v) for k, v in sorted(cc.items()))))
    L("    ⇒ 外出(3) 与 休息(7) **只会给 1 点或 0 点**；保健室/入院(8) **恒 0 点**；")
    L("      只有比赛(4) 出现 2/3/4 点 ⇒「目标赛 +4」= ct4 总点数 4 的那些行。")
    L("")
    L("  命令↔动作映射（按形状自证；COMMAND-ID-MAP.md 硬规矩：**禁止按序号外推训练项目**）：")
    for kk, n in sorted(meta["exec_census"].items(), key=lambda x: (x[0][0], str(x[0]))):
        L("    exec_command(type=%s, command_id=%s, group=%s, select=%s) × %d"
          % (kk[0], kk[1], kk[2], kk[3], n))
    L("    （1=训练；3=外出系；7=休息 701；8=保健室/入院 801；type=4 比赛不走 exec_command，走 race_* 请求）")
    L("")
    L("  普通外出 vs 友人外出：区分字段 = command_group_id")
    L("    %-8s %-10s %-48s %s" % ("group", "exec 次数", "结算 vital / 实落槽", "ΔLv 动格数分布"))
    for g, n in sorted(meta["grp_census"].items()):
        L("    %-8s %-10s %-48s %s" % (g, n, (meta["grp_vital"].get(g) or "—")[:48],
                                       meta["grp_lv"].get(g, "—")))
    L("    表侧 single_mode_outing（condition=1 只有 390 ⇒ 需要后援卡在场 = 友人外出）：")
    for rr in anchors["outing"]:
        L("      id=%-3s command_group_id=%-6s condition=%s is_play_cutt=%s" % rr)
    L("    表侧 single_mode_outing_set：%s" % (anchors["outing_set"],))
    L("    表侧 single_mode_15_act_info（act_type→稽古 command_id）：%s" % (anchors["act_info"],))
    L("")
    L("## 2. Q-A 主判据 = 预告落点本身（不是前后差分）")
    L("  U07（docs/matsuri_memo_cn.md:1058，user 2026-10-30 定案）：")
    L("    ① 演目 Lv 最低 → ② 槽最低 → ③ 仍相同按属性序")
    L("    ⚠ memo 字面第③级 = 「速/耐/力/毅力/智」，本次 brief = 「速/耐/根(毅力)/力/智」⇒ 力/毅力顺序不一致。")
    L("      报文只有 act_type（1–5）看不到属性名 ⇒ 本判据用 act_type 升序代表第③级；")
    L("      该分歧报文裁决不了（理由见 §8-①）。")
    L("  模型 dist()：把 k 点**逐点**派发 k 次；每次 argmin(Lv, 当前槽, 批次开始时的槽, act_type)；")
    L("    满槽（act_gauge >= need_act_gauge = 5）不入 ⇒ 不溢出。第③级「批次开始时的槽」是")
    L("    本库自证的平票破法（= 上一轮没被填满的格继续优先），**不是**友人指针优先，见 §6 对照。")
    L("")
    L("  %-26s %-6s %-6s %-8s %s" % ("command_type", "支持", "违例", "判不了", "违例率"))
    tt = [0, 0, 0]
    for ct in GA_TYPES:
        s, c, n = (tally["QA主判"][(ct, "支持")], tally["QA主判"][(ct, "违例")],
                   tally["QA主判"][(ct, "判不了")])
        tt = [tt[0] + s, tt[1] + c, tt[2] + n]
        L("  %-26s %-6s %-6s %-8s %s" % ("%d = %s" % (ct, GA_NAME[ct]), s, c, n,
                                        ("%.2f%%" % (100.0 * c / (s + c))) if (s + c) else "—"))
    L("  %-26s %-6s %-6s %-8s" % ("合计", tt[0], tt[1], tt[2]))
    L("")
    bad = [(r, ct) for r in rows for ct in GA_TYPES if r.get("主判%d" % ct) == "违例"]
    L("  违例全列（%d 条）：" % len(bad))
    for r, ct in bad:
        L("    [%s] 面板 %2d/引擎 %2d ct=%d(%s) 板Lv=%s 板槽=%s 板F=%s ⇒ %s  帧=%s"
          % (r["run"], r["t面板"], r["t引擎"], ct, SRC_OF_CT[ct], r["板_Lv"], r["板_槽"], r["板_F"],
             r["主判%d说明" % ct], r["board_file"]))
    if not bad:
        L("    （无）")
    L("")
    L("  判不了构成（共 %d 格）：" % sum(rs.values()))
    for (ct, why), n in sorted(rs.items(), key=lambda x: (-x[1], str(x[0]))):
        L("    ct=%-3s × %-5d %s" % (ct, n, why))
    if not rs:
        L("    （无）")
    L("")
    L("## 3. Q-A 次判据 = 真实落点（前后差分，只当交叉验证）")
    L("  差分：本回合**最后一个带预告四源的帧** → 下一面板回合第一条带演目板的帧。")
    L("  ⚠ 动作身份按**差分签名**判，不按 exec_command 的请求标签判：抓包流里两条 run 交错")
    L("    （同一 02:48:48 前缀下既有 run@171 的 _0001 也有另一条 run 的 _2175），")
    L("    请求标签实测把 %d 个友人外出标成了别的动作（见「标签不符」列）。"
      % sum(tally["标签不符"].values()))
    L("  污染行一律判不了，不硬算：落地前有负向差分（训练结算花槽）／板与落地之间夹着别的状态帧。")
    L("  %-18s %-6s %-6s %-8s" % ("差分签名", "支持", "违例", "判不了"))
    for act in sorted({k[0] for k in tally["QA次判"]}):
        L("  %-18s %-6s %-6s %-8s" % (act, tally["QA次判"][(act, "支持")],
                                      tally["QA次判"][(act, "违例")], tally["QA次判"][(act, "判不了")]))
    L("    友人外出的时序：%d 手按「+Lv 之后重算」命中，%d 手沿用「+Lv 之前的旧板」命中"
      % (tally["QA次判时序"]["按新状态重算"], tally["QA次判时序"]["沿用旧板"]))
    L("")
    nb = [r for r in rows if r.get("次判") == "违例"]
    L("  次判违例 %d 条（前 60）：" % len(nb))
    for r in nb[:60]:
        L("    [%s] 面板 %2d/引擎 %2d %-12s 预告=%-28s 实落=%-8s 落点=%-12s 帧=%s→%s"
          % (r["run"], r["t面板"], r["t引擎"], r.get("签名"), r.get("次判预告"), r.get("次判实落"),
             r.get("次判落点"), r.get("板帧"), r.get("落地文件")))
    if len(nb) > 60:
        L("    …（其余 %d 条见 CSV 次判 列）" % (len(nb) - 60))
    L("")
    nk = [r for r in rows if r.get("次判") == "判不了"]
    L("  次判判不了 %d 条（只列前 20，全量在 CSV）：" % len(nk))
    for r in nk[:20]:
        L("    [%s] 面板 %2d %-12s 预告=%-28s 实落=%-8s" % (r["run"], r["t面板"], r.get("签名"),
                                                       r.get("次判预告"), r.get("次判实落")))
    L("")
    L("## 4. Q-B 主判据 = friend_outing_gain_act_level 指针")
    L("  档案 _disasm_scen15/docs/SCENARIO-15.md:178：single_mode_15_outing_effect 只有")
    L("  support_card_id / story_step / add_level 三列业务字段，**没有 act_type 列**；选哪一格的逻辑在")
    L("  DialogSingleModeScenarioFestaOutingPartnerSelectItemViewModel 的")
    L("  ActTypeLevelUpByFriendOutingDict（比较规则档案自列 [无证]）。")
    L("  报文里该 dict 的运行时投影 = act_info_array[].friend_outing_gain_act_level ⇒「哪一格」只能这么读。")
    L("")
    L("  指针形状（全库逐报文，被标记的演目格数）：")
    for kk, n in sorted(meta["f_census"].items()):
        L("    标记 %s 格 ⇒ %d 个报文" % (kk, n))
    L("    ⇒ 只有 0 / 1 两种 ⇒ **一次友人外出只预约一格**（是指针，不是计数器）。")
    L("")
    L("  指针 = argmin(Lv → 槽 → act_type) 吗（逐裁决行三态）：")
    for v in ("支持", "违例", "判不了"):
        L("    %-4s N=%d" % (v, tally["QB指针"][v]))
    L("    「判不了」= 该行 F 全 0（该局/该回合无友人外出卡在场）或无预告板；全库 F 全 0 的报文 N=%d。"
      % meta["f_zero"])
    L("")
    pb = [r for r in rows if r.get("QB_指针判") == "违例"]
    L("  指针违例 %d 条：" % len(pb))
    for r in pb[:80]:
        L("    [%s] 面板 %2d/引擎 %2d %s 板Lv=%s 板槽=%s 帧=%s"
          % (r["run"], r["t面板"], r["t引擎"], r["QB_指针说明"], r["板_Lv"], r["板_槽"], r["board_file"]))
    if len(pb) > 80:
        L("    …（其余 %d 条见 CSV）" % (len(pb) - 80))
    L("")
    L("  指针格 与 同报文 ct=3（外出）预告槽落点格 是否同一格：")
    L("    同一格 N=%d / 不同格 N=%d / 不可比（ct3 预告空或无板）N=%d"
      % (meta["f_vs_ct3"][0], meta["f_vs_ct3"][1], meta["f_vs_ct3"][2]))
    L("    ⇒ 两个字段是**两件事**：F 指针 = 「下一级给谁」（Lv 落点），ct3 预告 = 「下一点槽给谁」；")
    L("      不同格的那些行正是 §3 的时序证据（+Lv 之后 argmin 变了）。")
    L("  ⚠ F 指针**不带满槽排除**：实测有 F 落在 act_gauge = 5（已满）的格上")
    L("      （如 190927_0190 那帧 F 指向槽已满的 act2）⇒ F 的键 = argmin(Lv, 槽, act_type)，")
    L("      不排除满槽；而送槽的 argmin 排除满槽。两条 argmin 不是同一条，引擎里别混用。")
    L("")
    L("## 5. Q-B 落地：真实外出这一手到底动了几个 act_level 格")
    L("  口径：板帧 → 落地帧 的 ΔLv，按**差分签名**归因（请求标签只做旁注，见 §3 红线）。")
    L("  全库差分签名统计：动 1 格 Lv 的干净落点 N=%d，动 ≥2 格 Lv 的 N=%d（后者全是合宿结束 confound，"
      % (meta["lv_single_rows"], meta["lv_multi_rows"]))
    L("  已单列剔除：合宿结束那一包把五格 Lv 同时 +1，本库 %d 条，判「判不了」不归于外出。"
      % len(confound))
    L("")
    L("  %-18s %-6s %-6s %-8s %s" % ("差分签名", "支持", "违例", "判不了", "「支持」的含义"))
    for act in sorted({k[0] for k in tally["QB落地"]}):
        L("  %-18s %-6s %-6s %-8s %s" % (act, tally["QB落地"][(act, "支持")],
                                         tally["QB落地"][(act, "违例")],
                                         tally["QB落地"][(act, "判不了")],
                                         "动 1 格且 = F 指针格" if act == "友人外出"
                                         else ("动 0 格（普通外出/休息/比赛不送 Lv）" if act.startswith("普通外出")
                                               else "不动 Lv"))
                                       )
    L("")
    L("  逐手全量明细（%d 手）：" % len(qb))
    for x in qb:
        L("    [%s] 面板 %2d/引擎 %2d %-12s ΔLv=%-16s Δ槽=%-16s F=act%-3s %-4s %s 帧=%s→%s"
          % (x["run"], x["t面板"], x["t引擎"], x["act"],
             ("|".join("%d:+%d" % (a, v) for a, v in sorted(x["dl"].items()) if v)) or "无",
             ("|".join("%d:+%d" % (a, v) for a, v in sorted(x["dg"].items()) if v)) or "无",
             x["marked"] or "-", x["v"], x["why"], x["file"], x["land_file"]))
    L("")
    L("  合宿 confound（%d 条，已从归因剔除）：" % len(confound))
    for x in confound:
        L("    [%s] 面板 %2d %-12s 五格齐 +1 落地帧=%s" % (x["run"], x["t面板"], x["act"], x["file"]))
    if not confound:
        L("    （无）")
    L("")
    L("  表侧锚 single_mode_15_outing_effect（4 列，无 act_type）：")
    L("    %-16s %-6s %-18s %s" % ("support_card_id", "段数", "story_step", "add_level"))
    for rr in anchors["outing_effect"]:
        L("    %-16s %-6s %-18s %s" % rr)
    L("    ⇒ 2 张卡 × 5 段、每段 add_level = 1 ⇒ 一整条友人外出剧情 = 5 次各 +1，一次一格；")
    L("      表侧给的是**次数锚**（每段 1 级），不是落点锚；落点只能靠报文指针。")
    L("")
    L("## 6. 目标赛 +4：是 1+1+1+1 还是 3+1？")
    L("  判据 = ct=4 预告总点数 = 4 的行（N=%d）。k=4 只出现在固定面板回合 ⇒ 那些是目标赛；" % len(k4))
    L("  非目标赛 k=1（同一字段、同一规则，只是数额不同）。")
    L("  k=4 出现的面板回合（去重排序）：%s" % (sorted({x["t面板"] for x in k4}),))
    L("  k=1 出现的面板回合（去重排序）：%s"
      % (sorted({r["t面板"] for r in rows if r.get("预告4")
                 and sum(int(x.split("+")[1]) for x in r["预告4"].split("|")) == 1}),))
    L("")
    L("  服务端给出的形状分布（点数降序拼串，同格已合并）：")
    for s, n in sorted(g4.items(), key=lambda x: (-x[1], x[0])):
        L("    %-12s N=%d" % (s, n))
    L("    全给 1 格(4) N=%d ／ 3+1 族 N=%d ／ 2+2 族 N=%d ／ 2+1+1 族 N=%d ／ 1+1+1+1 N=%d"
      % (g4.get("4", 0),
         g4.get("3+1", 0) + g4.get("1+3", 0),
         g4.get("2+2", 0),
         g4.get("2+1+1", 0) + g4.get("1+2+1", 0) + g4.get("1+1+2", 0),
         g4.get("1+1+1+1", 0)))
    L("")
    L("  模型对照（同一 k=4 集合，三种平票破法各命中多少）：")
    for nm, hit, why in meta["k4_models"]:
        L("    %-42s 命中 %-4d / %-4d   %s" % (nm, hit, len(k4), why))
    L("    轮转假设（4 点必给 4 个不同演目）命中：%d / %d" % (meta["k4_rr"], len(k4)))
    L("")
    L("  逐条 k=4 明细：")
    for x in k4:
        L("    [%s] 面板 %2d/引擎 %2d Lv=%-16s 槽=%-16s F=%-3s 预告=%-10s 模型=%-10s %-4s 帧=%s"
          % (x["run"], x["t面板"], x["t引擎"],
             "|".join("%d:%d" % (a, x["acts"][a][0]) for a in ACT_TYPES if a in x["acts"]),
             "|".join("%d:%d" % (a, x["acts"][a][1]) for a in ACT_TYPES if a in x["acts"]),
             next((str(a) for a in ACT_TYPES if a in x["acts"] and x["acts"][a][2]), "-"),
             x["shape_real"], x["shape_mdl"], x["v"], x["file"]))
    L("")
    L("  ⇒ 裁决：**「固定 3+1」与「固定 1+1+1+1」都不是规则**。")
    L("     规则仍是 U07 那条 argmin，只是 +4 要**逐点派发 4 次**、每一点重新看已加过的新槽；")
    L("     第③级平票破法 = **批次开始时槽更低者优先**（memo/U07 没写这层，是本次报文自证的新证据；")
    L("     「友人指针优先」这一层被否：命中 %d/%d，远低于主模型的 %d/%d，见上表）。"
      % (meta["k4_models"][1][1], len(k4), meta["k4_models"][0][1], len(k4)))
    L("     板状态决定形状：全场同 Lv 同槽 ⇒ 1+1+1+1（N=%d）；某格明显低 ⇒ 4 或 3+1（N=%d）。"
      % (g4.get("1+1+1+1", 0), g4.get("4", 0) + g4.get("3+1", 0) + g4.get("1+3", 0)))
    L("     docs/交接_上游合并重活_20261102.md:161 记的「真机两次都是 3+1」（B1 悬案）")
    L("     在本库有出处（3+1 族 N=%d），但那是**状态使然**不是规则 ⇒ B1 可结：两种形状同源。"
      % (g4.get("3+1", 0) + g4.get("1+3", 0)))
    L("")
    L("## 7. 与引擎现状的差（只陈述，不改 Rust）")
    k1s, k1d = meta["k1_flag_sensitivity"]
    k1n = sum(v for k, v in tally["QA主判"].items() if k[0] in (3, 7) and k[1] != "判不了")
    L("  mod.rs:1261 push_act_gauge(times)：")
    L("    已实现「① Lv 最低 → ② 槽最低 → ③ 下标升序」+ 满槽跳过 ⇒ 单点源（休息/外出/非目标赛）")
    L("    与预告全等（§2 ct3、ct7 违例 0 条）。")
    L("    键 = (Lv, 当前槽) 再按下标升序，逐点派发、满槽跳过 ⇒ 与主模型只差第③级。")
    L("    k=1 的 %d 格里主模型命中 %d 格，且**两种破法在 k=1 恒给出同一格**（差异 %d 格）："
      % (k1n, k1s, k1d))
    L("      第一点派发时「当前槽」就是「批次开始时的槽」，第③级不可能被用到 ⇒ 单点源无需改。")
    L("    k=4 时第③级被暴露：主模型（批次开始时的槽）命中 %d/%d，"
      % (meta["k4_models"][0][1], len(k4)))
    L("      友人指针破法 %d/%d，现引擎形状（只用当前槽+下标）%d/%d。"
      % (meta["k4_models"][1][1], len(k4), meta["k4_models"][2][1], len(k4)))
    L("    ⇒ push_act_gauge(+1) 与报文全等；push_act_gauge(+4) 复现不出 §6 的 3+1/2+2 形状，"
      "缺的就是第③级「批次开始时的槽」。")
    L("  mod.rs:383 do_outing() 里的 rng.random_range(0..5) 随机 +1：")
    L("    报文侧友人外出（签名 = 友人外出）N=%d 手，动 **1 格** N=%d、动 ≥2 格 N=%d；"
      % (meta["n390"], meta["qb390_one"], meta["qb390_ge2"]))
    L("    全库干净落点里动 ≥2 格 Lv 的只有合宿 confound（N=%d 条，五格齐 +1）⇒ 随机那格**无一手出处**。"
      % len(confound))
    L("    普通外出（301/304）一格都不动：支持 N=%d —— 本库只有这 %d 个正样本（exec 里 301×1、304×5），"
      % (meta["qb_norm_zero"], meta["qb_norm_zero"]))
    L("    **样本不足以裁决「普通方一定不动 Lv」**，只能说本库全 0。")
    L("    引擎 Action::Outing（action.rs:53-59）却同时调 do_outing()（随机 1 格）")
    L("    和 bump_lowest_act_level()（argmin 1 格）⇒ 一手送 **2 级**，随机那格无一手出处")
    L("    ⇒ 判 **虚高待删**（与 no_event_turns、vital+30 同族占位）。")
    L("    删掉随机行后 Action::Outing 应按 command_group_id 分叉：")
    L("      390 = 友人外出 → 先 argmin/F 格 +1 级（= bump_lowest_act_level），**再**按更新后的板 +1 槽")
    L("        （§3 时序：%d 手按新状态重算命中，%d 手沿用旧板）；"
      % (tally["QA次判时序"]["按新状态重算"], tally["QA次判时序"]["沿用旧板"]))
    L("      301/304 = 普通外出 → 只 +1 槽，不动 Lv（N=%d，见上面的小 N 警告）。" % meta["qb_norm_zero"])
    L("  另：引擎 Self::Clinic（入院）不调 push_act_gauge ⇒ 与报文「ct8 恒 0 点」一致，这条是对的。")
    L("")
    L("## 8. 判不了的原因与下一步需要什么一手")
    zero_board = sum(n for (ct, why), n in rs.items() if "全 0 板" in str(why))
    noboard = sum(n for (ct, why), n in rs.items() if "没有 command_gain" in str(why))
    L("  ① U07 第③级的属性顺序（memo「速/耐/力/毅力/智」vs brief「速/耐/根/力/智」）：")
    L("     报文只有 act_type 1–5，没有属性名。single_mode_15_act_info 只给 act_type→稽古 command_id")
    L("     （1..5 → 5101..5105，见 §1），而 COMMAND-ID-MAP.md 记 5104/5105 的耐力/根性归属仍未证")
    L("     ⇒ **本条不足以裁决**。要定它需要 act_type ↔ 五维 的一手（稽古格 params_inc_dec_info_array")
    L("     主属性 × act_info 的双向咬合）。本报告不猜。")
    L("  ② 「一次友人外出会不会给 2 格」：本库签名=友人外出 N=%d 手（%d/%d 局出现过）全是 1 格；"
      % (meta["n390"], meta["runs_with_390"], len(meta["turns"])))
    L("     表侧 single_mode_15_outing_effect 每段 add_level=1 也咬合。但只有 2 张卡（10149/30323）×5 段")
    L("     ⇒ **卡样本太少**，「所有友人外出卡都只给 1 格」只能说本库无反证，不宣称穷尽。")
    L("  ③ F 指针全 0 的行（全库报文 N=%d）= 该局没有友人外出卡在场 ⇒ 这些行 Q-B 判不了；"
      % meta["f_zero"])
    L("     扩样本需要带 10149 或 30323 的对局（本库仅 %d/%d 局有 390 手）。"
      % (meta["runs_with_390"], len(meta["turns"])))
    L("  ④ 开局/载入态「全 0 板」（Lv 全 1、槽全 0 却预告空）N=%d 格：那是 single_mode_load_common"
      % zero_board)
    L("     携带的未初始化副本，不能当「满槽不溢出」的反证 ⇒ 记判不了。")
    L("  ⑤ 报文里根本没有 command_gain_act_gauge_array 的回合 N=%d 格（赛事动画/事件链的中间报文）"
      % noboard)
    L("     ⇒ 判不了，不参与 §2 分母。")
    L("  ⑥ 训练源送的槽不在 U07 四源内，本次不裁（训练格预告走 command_info_array[].")
    L("     predict_gain_act_gauge_array，全库非空 N=%d 次；它是「花 5 点槽换 1 级」的另一条规则）。"
      % meta["cell_ga_n"])
    L("  ⑦ 次判据里「判不了」的三类污染（逐条原因写进 CSV 的 次判说明 列）：")
    for nm, n in sorted(tally["QA次判污染"].items(), key=lambda x: -x[1]):
        L("     %s N=%d" % (nm, n))
    L("     根因：抓包流把**两条 run 交错**写进同一目录（同一 HHMMSS 前缀下既有 _0001 也有 _2175），")
    L("     exec_command 的请求标签与 resp 的 run 归属并不总是一致 ⇒ 本表改用差分签名，")
    L("     并记「标签不符」N=%d 条。要彻底修需要**按连接拆包**的一手（报文里没有 conn id，")
    L("     只能靠文件名序号段猜 ⇒ 不足以裁决，不猜）。")
    finale_cells = sum(1 for r in rows if r.get("决胜期") for ct in GA_TYPES if r.get("主判%d" % ct) == "判不了")
    L("  ⑧ 决胜期（面板 %d–%d）四源预告全空 N=%d 格：本库该区间从未出现「外出/休息/入院」的请求"
      % (FINALE_PANEL_FROM, 78, finale_cells))
    L("     （exec 只有训练 ct1，比赛只在 70/71/72/74/76/78）⇒ 分不开「该源不可选」与「该源不送槽」，")
    L("     一律判不了，不参与 §2 的违例分母。")
    L("")
    L("## 9. 三态汇总")
    L("  Q-A 主判据（预告落点，四源）  支持 %d / 违例 %d / 判不了 %d   （4 × %d 行 = %d 格）"
      % (tt[0], tt[1], tt[2], len(rows), 4 * len(rows)))
    L("  Q-A 单点源 ct3+ct7（休息/外出/非目标赛）  支持 %d / 违例 %d / 判不了 %d"
      % (sum(tally["QA主判"][(c, "支持")] for c in (3, 7)),
         sum(tally["QA主判"][(c, "违例")] for c in (3, 7)),
         sum(tally["QA主判"][(c, "判不了")] for c in (3, 7))))
    L("  Q-A ct4（比赛，含目标赛 +4）  支持 %d / 违例 %d / 判不了 %d"
      % (tally["QA主判"][(4, "支持")], tally["QA主判"][(4, "违例")], tally["QA主判"][(4, "判不了")]))
    L("  Q-A ct8（入院）恒 0 点         支持 %d / 违例 %d / 判不了 %d"
      % (tally["QA主判"][(8, "支持")], tally["QA主判"][(8, "违例")], tally["QA主判"][(8, "判不了")]))
    L("  Q-A 次判据（真实差分）         支持 %d / 违例 %d"
      % (sum(v for k, v in tally["QA次判"].items() if k[1] == "支持"),
         sum(v for k, v in tally["QA次判"].items() if k[1] == "违例")))
    L("  Q-B 指针 = argmin              支持 %d / 违例 %d / 判不了 %d"
      % (tally["QB指针"]["支持"], tally["QB指针"]["违例"], tally["QB指针"]["判不了"]))
    L("  Q-B 外出落地（动几格）         支持 %d / 违例 %d / 判不了 %d"
      % (sum(v for k, v in tally["QB落地"].items() if k[1] == "支持"),
         sum(v for k, v in tally["QB落地"].items() if k[1] == "违例"),
         sum(v for k, v in tally["QB落地"].items() if k[1] == "判不了")))
    L("  目标赛 +4                     k=4 行 N=%d，主模型命中 %d/%d，形状分布见 §6"
      % (len(k4), meta["k4_models"][0][1], len(k4)))
    L("")
    L("（完）重跑：python tools/outing_act_level_scan.py --viewer %s --scen %s"
      % (args.viewer, args.scen))
    return "\n".join(o) + "\n"


def main():
    ap = argparse.ArgumentParser(description="演目槽/Lv 赠送落点报文核对（#58）")
    ap.add_argument("--viewer", type=int, default=238107205)
    ap.add_argument("--scen", type=int, default=15)
    ap.add_argument("--from", dest="lo", type=int, default=1)
    ap.add_argument("--to", dest="hi", type=int, default=78)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    resp, req = load_stream(args.viewer, args.scen, args.lo, args.hi, verbose=not args.quiet)
    runs = index_by_run(resp, req)
    rows, tally, ga_shape, k4, qb, confound = adjudicate(runs, args)
    anchors = mdb_anchors()
    meta = build_meta(resp, req, runs, rows, k4, qb, anchors)

    write_csv(rows, OUT_CSV)
    txt = render(args, rows, tally, ga_shape, k4, qb, confound, anchors, meta)
    with open(OUT_TXT, "w", encoding="utf-8") as fh:
        fh.write(txt)
    if not args.quiet:
        sys.stdout.write(txt)
    sys.stderr.write("\n写出：%s（%d 行）/ %s\n" % (OUT_CSV, len(rows), OUT_TXT))


if __name__ == "__main__":
    main()
