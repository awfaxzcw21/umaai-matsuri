#!/usr/bin/env python3
"""高分（LF 段）局的一手逐回合报文覆盖普查。

一条命令复现 `docs/高分局报文覆盖_20261104.md` 里所有数字：

    python tools/lf_packet_scan.py                 # 全量扫（约 3~4 分钟）+ 出报告 + 落 CSV/JSON
    python tools/lf_packet_scan.py --from-cache    # 复用上次扫描的缓存，只重出报告
    python tools/lf_packet_scan.py --min-score 91400 --top 15

三类一手证据分得很清，不混用：
  A 逐回合轨迹 = 响应里的 `data.chara_info`（自己的育成局，一个 (viewer,chara,start_time,turn) 一行）
  B 终局快照   = 任何带 `rank_score` 的练成马娘（排行榜/目录/备援马娘…，含**官方评价点**，但没有逐回合）
  C 请求意图   = `*_request_common` 报文体（玩家实际下的手）

A 的分数是**派生量**：`Σ five_status_final_score[revise(五维)] + k × Σ grade_value(逐技能 id)`，
与 `tools/lf_target.py` 同一口径（k=1.0746，剧本 15；那张 LF 卡用这套查表对官方 rank_score 零差）。
B 的分数是报文自带的官方值，直接读。
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
import time

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402  复用 sort_key / CHANNELS / get_ci
import train_leg_preview as TP  # noqa: E402  复用 preview_of / camp_of / heads_of / PLAIN / KEIKO

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw"
CONST = "E:/HJM/_s7x2/data/constants.json"
MASTER = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
CACHE = "out/_lf_pkt_cache_20261104.json"
AXES = ("speed", "stamina", "power", "guts", "wiz")
K_SCEN15 = 1.0746          # 剧本 15 技能分系数（lf_target.py 同一常数）
LF_LINE = 91400            # 本地两份一手档位表的 LF 起线


def revise(v):
    return v if v <= 1200 else 2 * v - 1200


def score_tables():
    tbl = json.load(open(CONST, encoding="utf-8"))["five_status_final_score"]
    db = sqlite3.connect("file:%s?mode=ro" % MASTER.replace("\\", "/"), uri=True)
    grade = dict(db.execute("select id, grade_value from skill_data"))
    db.close()
    return tbl, grade


def stat_score(tbl, five):
    last = len(tbl) - 1
    return sum(tbl[min(int(revise(v or 0)), last)] for v in five)


def grade_sum(grade, skills):
    ids = {s.get("skill_id") for s in skills or [] if isinstance(s, dict) and s.get("skill_id")}
    return int(sum(grade.get(i, 0) or 0 for i in ids)), len(ids)


def auto_declare(asi):
    """`festa_data_set.auto_setting_info` → (选中的 preset_id, 声明条目数, AttachAdvice 指纹).

    这是报文里唯一「玩家在育成前就配好的意图声明」（游戏里的 教えセット）。
    指纹 = 每个 id 在各演目(act_type)上要贴的 advice_id；融合声明单独记。
    """
    if not isinstance(asi, dict):
        return None, 0, None
    presets = [p for p in (asi.get("preset_array") or []) if isinstance(p, dict)]
    n_decl = sum(len(p.get("advice_info_array") or []) + len(p.get("auto_setting_attach_advice_info_array") or [])
                 for p in presets)
    called = asi.get("auto_call_preset_id")
    hit = next((p for p in presets if p.get("preset_id") == called), None) if called else None
    if hit is None:
        return called, n_decl, None
    attach = tuple(sorted(
        (x.get("id"),
         tuple(sorted((y.get("act_type"), y.get("advice_id"))
                      for y in (x.get("attach_advice_info_array") or []) if isinstance(y, dict))))
        for x in (hit.get("auto_setting_attach_advice_info_array") or []) if isinstance(x, dict)))
    fusion = tuple(sorted((a.get("advice_id"), a.get("fusion_advice_id"))
                          for a in (hit.get("advice_info_array") or []) if isinstance(a, dict)))
    return called, n_decl, (fusion, attach)


# ---------------------------------------------------------------- 扫描

def scan(limit=0):
    """单遍扫全部报文，产出 cache dict。limit>0 时只取前 N 个（冒烟用）。"""
    paths = sorted(glob.glob(os.path.join(RAW, "*.bin")), key=PS.sort_key)
    if limit:
        paths = paths[:limit]
    tbl, grade = score_tables()
    runs = {}               # key -> run record
    turns = {}              # (runkey, turn) -> state（保留该回合最后一个快照）
    board = []              # 板上报文逐条目（沿用 train_leg_preview 的列口径）
    snaps = []              # 带 rank_score 的练成马娘终局快照
    req_keys = collections.Counter()
    req_intent = collections.Counter()   # 请求体里 act/select/target/plan 相关键
    req_body_keys = collections.Counter()  # 局内请求体的全部键（判「意图字段」用）
    # 「执行动作那一刻，五个演目各自的 act_training_state」——判 act_training_state 是不是玩家意图
    act_state_of_keiko = collections.Counter()   # (被执行的 act_type, 该演目当时的 state) -> n
    act_state_sig = collections.Counter()        # (动作类别, state 向量) -> n
    auto_decl_by_run = collections.defaultdict(collections.Counter)  # run -> {宣告済み/空: n（去重到唯一 (局,回合)）}
    auto_matrix = collections.defaultdict(collections.Counter)  # (run, preset_id) -> AttachAdvice 指纹 -> n
    cur_acts = None                              # 最近一份快照的 (chara, turn, {act_type: state})
    cur_run = None                               # 最近一份快照所属的 run 名（给请求包归属用）
    plan_by_run = collections.defaultdict(collections.Counter)  # run -> {exec_auto_play_plan_id: n}
    stats = collections.Counter()
    roadmap = collections.Counter()              # 育成响应里 training_roadmap_selected_info 的形态

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
        viewer = d.get("viewer_id")
        if viewer is None:
            viewer = (d.get("data_headers") or {}).get("viewer_id") if isinstance(d.get("data_headers"), dict) else None

        if name.endswith("_notify_request.bin"):
            stats["req_files"] += 1
            for k in d:
                if isinstance(k, str) and k.endswith("_request_common"):
                    req_keys[k] += 1
                    body = d[k]
                    if isinstance(body, dict):
                        if k.startswith("single_mode"):
                            for bk in body:
                                req_body_keys["%s.%s" % (k, bk)] += 1
                        for bk in body:
                            if isinstance(bk, str) and any(
                                    s in bk.lower() for s in ("act", "select", "target", "plan", "intent")):
                                req_intent["%s.%s" % (k, bk)] += 1
                        if k == "single_mode_exec_command_request_common":
                            plan_by_run[cur_run or "??"][body.get("exec_auto_play_plan_id")] += 1
                        if cur_acts and k == "single_mode_exec_command_request_common":
                            cid = body.get("command_id")
                            if body.get("command_type") == 1 and isinstance(cid, int):
                                at = cid - 5100 if cid in TP.KEIKO else None
                                state_sig = tuple(cur_acts[2].get(i) for i in range(1, 6))
                                cat = ("keiko" if at else
                                       ("plain" if cid in TP.PLAIN else "other"))
                                act_state_sig[(cat, state_sig)] += 1
                                if at:
                                    act_state_of_keiko[(at, cur_acts[2].get(at))] += 1
            continue

        data = d.get("data")
        if not isinstance(data, dict):
            continue
        rp = data.get("training_roadmap_selected_info", "键不存在")
        roadmap["null" if rp is None else ("键不存在" if rp == "键不存在" else "非空")] += 1

        # ---- 快照收集前先摘终局马娘（含嵌套目录），命中即不再下钻
        for snap, jpath in find_snaps(data, ""):
            snaps.append(snap_record(name, jpath, snap, tbl, grade))

        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        rk = (viewer, ci.get("single_mode_chara_id"), ci.get("start_time"))
        turn = ci.get("turn")
        five = [ci.get(a) for a in AXES]
        gs, ns = grade_sum(grade, ci.get("skill_array"))
        fs = data.get("festa_data_set") or {}
        fi = fs.get("festa_info") or {}
        acts = {a.get("act_type"): a for a in (fs.get("act_info_array") or []) if isinstance(a, dict)}
        tli = {e.get("command_id"): e.get("level")
               for e in (ci.get("training_level_info_array") or []) if isinstance(e, dict)}
        st_run = "%s@%s@%s" % rk
        called, n_decl, sig = auto_declare(fs.get("auto_setting_info"))
        cur_acts = (ci.get("single_mode_chara_id"), turn,
                    {a: (v.get("act_training_state")) for a, v in acts.items()})
        cur_run = st_run
        st = {
            "run": st_run, "viewer": viewer, "chara": ci.get("single_mode_chara_id"),
            "auto_call_preset_id": called, "auto_decl_n": n_decl, "auto_decl": sig,
            "start_time": ci.get("start_time"), "turn": turn, "file": name,
            "scenario_id": ci.get("scenario_id"), "route_id": ci.get("route_id"),
            "five": five, "maxes": [ci.get("max_" + a) for a in AXES],
            **dict(zip(AXES, five)),
            "card_id": ci.get("card_id"), "chara_grade": ci.get("chara_grade"),
            "rarity": ci.get("rarity"), "talent_level": ci.get("talent_level"),
            "five_sum": sum(v or 0 for v in five),
            "stat_score": stat_score(tbl, five), "sum_grade": gs, "n_skills": ns,
            "score": stat_score(tbl, five) + K_SCEN15 * gs,
            "skill_point": ci.get("skill_point"), "vital": ci.get("vital"),
            "motivation": ci.get("motivation"), "fans": ci.get("fans"),
            "max_speed": ci.get("max_speed"), "max_stamina": ci.get("max_stamina"),
            "max_power": ci.get("max_power"), "max_guts": ci.get("max_guts"),
            "max_wiz": ci.get("max_wiz"), "max_vital": ci.get("max_vital"),
            "dmax_speed": ci.get("default_max_speed"), "dmax_stamina": ci.get("default_max_stamina"),
            "dmax_power": ci.get("default_max_power"), "dmax_guts": ci.get("default_max_guts"),
            "dmax_wiz": ci.get("default_max_wiz"),
            "camp_left": fi.get("summer_camp_remain_turn_num"),
            "max_act_level": fi.get("max_act_level"), "act_training_step": fi.get("act_training_step"),
            "act_lv": {k: v.get("level") for k, v in acts.items()},
            "act_gauge": {k: v.get("act_gauge") for k, v in acts.items()},
            "act_need": {k: v.get("need_act_gauge") for k, v in acts.items()},
            "act_state": {k: v.get("act_training_state") for k, v in acts.items()},
            "act_advice": {k: v.get("advice_id") for k, v in acts.items()},
            "train_lv": tli,
            "n_eval": len(ci.get("evaluation_info_array") or []),
            "eval_sum": sum(e.get("evaluation") or 0 for e in (ci.get("evaluation_info_array") or [])
                            if isinstance(e, dict)),
            "n_cp": len(fs.get("check_point_result_array") or []),
            "race_program_id": ci.get("race_program_id"),
            "reserve_race_program_id": ci.get("reserve_race_program_id"),
            "playing_state": ci.get("playing_state"), "state": ci.get("state"),
            "deck": [s.get("support_card_id") for s in (ci.get("support_card_array") or [])
                     if isinstance(s, dict)],
        }
        turns[(rk, turn)] = st                      # 同回合后到的快照覆盖先到的
        r = runs.setdefault(rk, {"run": st["run"], "viewer": viewer, "chara": st["chara"],
                                 "start_time": st["start_time"], "scen": st["scenario_id"],
                                 "routes": collections.Counter(), "cards": collections.Counter(),
                                 "deck": collections.Counter(), "turns": collections.Counter(),
                                 "files": [name, name], "n_snap": 0})
        r["routes"][st["route_id"]] += 1
        r["cards"][st["card_id"]] += 1
        for c in st["deck"]:
            if c:
                r["deck"][c] += 1
        r["turns"][turn] += 1
        r["n_snap"] += 1
        r["files"][1] = name

        # ---- 板上报文逐条目（训练/稽古预告）
        cia = (data.get("home_info") or {}).get("command_info_array")
        if not cia:
            continue
        fcia = {c.get("command_id"): c for c in (fs.get("command_info_array") or [])
                if isinstance(c, dict)}
        for c in cia:
            cid = c.get("command_id")
            if not (cid in TP.PLAIN or cid in TP.KEIKO):
                continue
            if not c.get("params_inc_dec_info_array"):
                continue
            pt, five_p, vit = TP.preview_of(c)
            if pt is None:
                continue
            fpt = None
            fe = fcia.get(cid)
            if fe and fe.get("params_inc_dec_info_array"):
                fpt = TP.preview_of(fe)[0]
            nd, nb, nt, nsub = TP.heads_of(c)
            board.append({
                "file": name, "run": st["run"], "turn": turn, "cid": cid,
                "kind": "keiko" if cid in TP.KEIKO else "plain", "camp": TP.camp_of(data),
                "level": c.get("level"), "heads_deck": nd, "heads_own": nd - nsub,
                "heads_sub": nsub, "heads_other": nb, "heads_total": nd + nb, "tips": nt,
                "failure_rate": c.get("failure_rate"), "is_enable": c.get("is_enable"),
                "pt": pt, "pt_festa": fpt,
                "speed": five_p[0], "stamina": five_p[1], "power": five_p[2], "guts": five_p[3],
                "wiz": five_p[4], "vital": vit, "sp_at_board": ci.get("skill_point"),
                "motivation": st["motivation"], "max_speed": st["max_speed"],
                "max_stamina": st["max_stamina"], "max_power": st["max_power"],
                "max_guts": st["max_guts"], "max_wiz": st["max_wiz"],
                "act_lv": "|".join("%s:%s" % (k, v) for k, v in sorted(
                    (st["act_lv"] or {}).items(), key=lambda x: str(x[0]))),
                "act_step": st["act_training_step"], "score_at_board": st["score"],
            })
    # 「意図宣言」与「命令是谁下的」按局汇总（逐回合状态已去重，所以这里天然一手一次/回合）
    for v in turns.values():
        c = auto_decl_by_run[v["run"]]
        c["call=%s" % v.get("auto_call_preset_id")] += 1
        c["decl" if v.get("auto_decl") else ("part" if v.get("auto_decl_n") else "none")] += 1
        if v.get("auto_decl"):
            auto_matrix[(v["run"], v.get("auto_call_preset_id"))][
                json.dumps(v["auto_decl"], ensure_ascii=False, sort_keys=True)] += 1
    return {"stats": dict(stats), "req_keys": dict(req_keys), "req_intent": dict(req_intent),
            "req_body_keys": dict(req_body_keys),
            "act_state_of_keiko": {"%s|%s" % k: v for k, v in act_state_of_keiko.items()},
            "act_state_sig": {"%s|%s" % k: v for k, v in act_state_sig.items()},
            "plan_by_run": {k: {str(pk): pv for pk, pv in c.items()} for k, c in plan_by_run.items()},
            "auto_decl_by_run": {k: dict(c) for k, c in auto_decl_by_run.items()},
            "auto_matrix": {"%s|%s" % k: dict(c) for k, c in auto_matrix.items()},
            "roadmap_form": dict(roadmap),
            "turns": {"%s|%s" % k: v for k, v in turns.items()},
            "runs": {("%s|%s|%s" % k): v for k, v in runs.items()},
            "board": board, "snaps": snaps}


def find_snaps(node, path, depth=0, out=None):
    """递归找「带 rank_score + speed 的练成马娘字典」，命中后不再下钻该子树。"""
    if out is None:
        out = []
    if depth > 7:
        return out
    if isinstance(node, dict):
        if "rank_score" in node and "speed" in node and "skill_array" in node:
            out.append((node, path))
            return out
        for k, v in node.items():
            find_snaps(v, path + "." + str(k), depth + 1, out)
    elif isinstance(node, list):
        for v in node:
            find_snaps(v, path + "[]", depth + 1, out)
    return out


def snap_record(pkt, jpath, tc, tbl, grade):
    five = [tc.get(a) for a in AXES]
    gs, ns = grade_sum(grade, tc.get("skill_array"))
    stat = stat_score(tbl, five)
    calc = stat + K_SCEN15 * gs
    return {"file": pkt, "jpath": jpath, "viewer": tc.get("viewer_id"),
            "owner": tc.get("owner_viewer_id"), "trained_chara_id": tc.get("trained_chara_id"),
            "chara": tc.get("single_mode_chara_id"), "card_id": tc.get("card_id"),
            "scenario_id": tc.get("scenario_id"), "route_id": tc.get("route_id"),
            "rank_score": tc.get("rank_score"), "rank": tc.get("rank"),
            "chara_grade": tc.get("chara_grade"), "rarity": tc.get("rarity"),
            "talent_level": tc.get("talent_level"),
            "speed": five[0], "stamina": five[1], "power": five[2], "guts": five[3], "wiz": five[4],
            "five_sum": sum(v or 0 for v in five), "fans": tc.get("fans"),
            "total_skill_pt": tc.get("total_skill_pt"), "n_skill_ids": ns, "sum_grade": gs,
            "stat_score": round(stat, 1), "calc_score": round(calc, 1),
            "diff_vs_official": round(calc - (tc.get("rank_score") or 0), 1),
            "create_time": tc.get("create_time"), "wins": len(tc.get("race_result_list") or []),
            "deck": [s.get("support_card_id") for s in (tc.get("support_card_list") or [])
                     if isinstance(s, dict)],
            "scenario_data": tc.get("scenario_data_array")}


# ---------------------------------------------------------------- 汇总

def top1(c):
    """众数键（Counter 或 JSON round-trip 后的普通 dict 都能用）。

    cache 落 JSON 后 dict 的键会被转成字符串，这里 cast 回 int，
    否则跑↔终局快照配对时 card_id 会 int vs str 恒不相等。
    """
    if not c:
        return None
    k = c.most_common(1)[0][0] if isinstance(c, collections.Counter) else max(c, key=lambda x: c[x])
    return int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k


def run_table(cache):
    """一个 run 一行：逐回合覆盖 + 分数轨迹（派生）。"""
    by_run = collections.defaultdict(list)
    for st in cache["turns"].values():
        by_run[st["run"]].append(st)
    rows = []
    for rkey, r in cache["runs"].items():
        ts = by_run.get(r["run"]) or []
        if not ts:
            continue
        tset = sorted({st["turn"] for st in ts})
        last = max(ts, key=lambda st: (st["turn"], st["file"]))
        rows.append({
            "run": r["run"], "viewer": r["viewer"], "chara": r["chara"], "scen": r["scen"],
            "start_time": r["start_time"], "card_id": top1(r["cards"]),
            "route_id": top1(r["routes"]),
            "t_min": tset[0], "t_max": tset[-1], "n_turns": len(tset),
            "turn_list": ",".join(str(t) for t in tset),
            "gap_turns": ",".join(str(t) for t in range(tset[0], tset[-1] + 1) if t not in tset),
            "first_file": r["files"][0], "last_file": r["files"][1], "n_snap": r["n_snap"],
            "five_sum_end": last["five_sum"], "stat_score_end": round(last["stat_score"], 1),
            "five_end": json.dumps(last["five"], ensure_ascii=False),
            "sum_grade_end": last["sum_grade"], "score_end": round(last["score"], 1),
            "skill_point_end": last["skill_point"], "motivation_end": last["motivation"],
            "max_speed_end": last["max_speed"], "camp_left_end": last["camp_left"],
            "act_lv_end": json.dumps(last["act_lv"], ensure_ascii=False),
            "n_turns_29_48": len({t for t in tset if 29 <= t <= 48}),
            "n_turns_37_44": len({t for t in tset if 37 <= t <= 44}),
            "is_lf_line": 1 if last["score"] >= LF_LINE else 0,
        })
    rows.sort(key=lambda r: -r["score_end"])
    return rows


def dist(vals, edges):
    c = collections.Counter()
    for v in vals:
        for lo, hi, tag in edges:
            if v is not None and lo <= v < hi:
                c[tag] += 1
                break
    return c


def probe_fields(snaps):
    """高分那几张：回源报文把「一手能读到什么」逐字段摊开（文档 §4 的出处）。"""
    print("\n-- LF 段最高分 %d 张：回源解码，一手可读字段清单 --" % len(snaps))
    for s in snaps:
        path = os.path.join(RAW, s["files"][0])
        if not os.path.exists(path):
            print("   来源报文已不在盘上：%s" % path)
            continue
        try:
            d = ml.decode_file(path)
        except Exception as e:  # noqa: BLE001
            print("   解码失败 %s：%s" % (path, e))
            continue
        hit = next((tc for tc, _ in find_snaps(d.get("data") or {}, "")
                    if tc.get("viewer_id") == s["viewer"]
                    and tc.get("trained_chara_id") == s["trained_chara_id"]
                    and tc.get("rank_score") == s["rank_score"]), None)
        if hit is None:
            print("   %s 里没再找到 rank_score=%s" % (s["files"][0], s["rank_score"]))
            continue
        rr = hit.get("race_result_list") or []
        deck = hit.get("support_card_list") or []
        print("   rank_score=%s｜viewer=%s｜card=%s｜route=%s｜create_time=%s｜%s"
              % (s["rank_score"], s["viewer"], s["card_id"], s["route_id"],
                 s.get("create_time"), s["files"][0]))
        print("     顶层字段 %d 个｜五维 %s（和 %s）｜total_skill_pt=%s｜技能 %d 条"
              % (len(hit), [hit.get(a) for a in AXES], sum(hit.get(a) or 0 for a in AXES),
                 hit.get("total_skill_pt"), len(hit.get("skill_array") or [])))
        print("     卡组 %d 张：exp=%s／限界break=%s"
              % (len(deck), sorted({x.get("exp") for x in deck}), sorted({x.get("limit_break_count") for x in deck})))
        print("     赛果 %d 场（带 turn）：%s" % (len(rr), [x.get("turn") for x in rr]))
        print("     因子 %d+%d｜继承 %d 头｜chara_seed=%s｜fans=%s｜scenario_data_array=%s"
              % (len(hit.get("factor_info_array") or []), len(hit.get("factor_extend_array") or []),
                 len(hit.get("succession_chara_array") or []), hit.get("chara_seed"),
                 hit.get("fans"), json.dumps(hit.get("scenario_data_array"), ensure_ascii=False)))
        print("     ⚠ 以上都是**终局快照**；回合间的五维/PT/演目/gauge 一个都读不到（逐回合 = 0 局，见结论 1）")


def report(cache, min_score, top):
    runs = run_table(cache)
    snaps = cache["snaps"]
    n_files = cache["stats"].get("files", 0)
    print("== 扫描面 ==")
    print("报文文件（解码成功）= %d｜其中请求包 %d／响应包 %d｜解码失败 %d｜局内请求体种类 %s"
          % (n_files, cache["stats"].get("req_files", 0),
             n_files - cache["stats"].get("req_files", 0),
             cache["stats"].get("decode_fail", 0),
             {k: v for k, v in cache["req_keys"].items() if k.startswith("single_mode")}))
    print("带 chara_info 逐回合快照的 run（自己的育成局）= %d｜回合快照行数 = %d"
          % (len(runs), sum(r["n_snap"] for r in runs)))
    scen_r = collections.Counter(r["scen"] for r in runs)
    print("run 的剧本分布：%s" % dict(scen_r))
    print("终局快照（带官方 rank_score 的练成马娘，去重前）= %d" % len(snaps))

    # 终局快照去重（同一 trained_chara_id 在多份报文里反复出现）
    uniq = {}
    for s in snaps:
        k = (s["viewer"], s["trained_chara_id"], s["rank_score"])
        u = uniq.setdefault(k, dict(s, n_seen=0, files=[]))
        u["n_seen"] += 1
        if len(u["files"]) < 3:
            u["files"].append(s["file"])
    us = sorted(uniq.values(), key=lambda s: -(s["rank_score"] or 0))
    print("去重后的练成马娘 = %d 张（scenario 分布 %s）"
          % (len(us), dict(collections.Counter(s["scenario_id"] for s in us))))

    lf = [s for s in us if (s["rank_score"] or 0) >= LF_LINE]
    # 局 ↔ 终局快照配对：同 viewer + 同卡 + 终局五维逐轴相等（配上了 = 这局的终值报文里直接读得到）
    matched = {}
    for r in runs:
        r["official_rank_score"] = None
        r["official_src"] = ""
        r["official_create_time"] = ""
        r["n_official_cand"] = 0
        five_end = [int(v or 0) for v in json.loads(r["five_end"])]
        # 认「同一匹马」不靠时间猜：同 viewer + 同卡 + **五维逐轴相等**，再看 create_time 早晚
        cand = [s for s in us if s["viewer"] == r["viewer"] and s["card_id"] == r["card_id"]
                and [int(s.get(a) or 0) for a in AXES] == five_end]
        r["n_official_cand"] = len(cand)
        if cand:
            best = min(cand, key=lambda s: (s.get("create_time") or ""))
            r["official_rank_score"] = best["rank_score"]
            r["official_src"] = "%s @ %s" % (best["files"][0], best["jpath"])
            r["official_create_time"] = best["create_time"]
            matched[id(best)] = r
    own_viewers = collections.Counter(r["viewer"] for r in runs)
    own_viewer = own_viewers.most_common(1)[0][0] if own_viewers else None
    print("\n自己账号（run 的 viewer）：%s" % dict(own_viewers))
    n_own_snap = sum(1 for s in us if s["viewer"] in own_viewers)
    print("属于自己账号的终局快照 = %d 张｜其中已配上逐回合轨迹的 = %d 张"
          % (n_own_snap, sum(1 for s in us if s["viewer"] in own_viewers and id(s) in matched)))
    print("\n== 一手终局快照里的 LF 段（官方 rank_score ≥ %d）==" % LF_LINE)
    print("张数 = %d｜scenario15 = %d" % (len(lf), sum(1 for s in lf if s["scenario_id"] == 15)))
    print("%-14s %-12s %-8s %-8s %-7s %-7s %-9s %-9s %s"
          % ("rank_score", "viewer", "scen", "route", "card", "五维和", "Σgrade", "有逐回合?", "来源报文"))
    for s in lf[:20]:
        print("%-14s %-12s %-8s %-8s %-7s %-7s %-9s %-9s %s"
              % (s["rank_score"], s["viewer"], s["scenario_id"], s["route_id"], s["card_id"],
                 s["five_sum"], s["sum_grade"], "有" if id(s) in matched else "无", s["files"][0]))

    print("LF 段来源路径分布：%s" % dict(collections.Counter(s["jpath"] for s in lf)))
    print("LF 段 viewer 数 = %d｜属于自己账号的 = %d｜route 分布 = %s"
          % (len({s["viewer"] for s in lf}), sum(1 for s in lf if s["viewer"] == own_viewer),
             dict(collections.Counter(s["route_id"] for s in lf).most_common(8))))
    print("LF 段里带逐回合轨迹的 = %d 条" % sum(1 for s in lf if id(s) in matched))
    rng = lambda k: (min(s[k] for s in lf), max(s[k] for s in lf))  # noqa: E731
    print("LF 段 card 前 5 = %s｜五维和 %s–%s｜Σgrade %s–%s｜total_skill_pt %s–%s"
          % (dict(collections.Counter(s["card_id"] for s in lf).most_common(5)),
             rng("five_sum")[0], rng("five_sum")[1], rng("sum_grade")[0], rng("sum_grade")[1],
             rng("total_skill_pt")[0], rng("total_skill_pt")[1]))
    probe_fields(lf[:3])

    def v15(pred):
        return sorted({x.get("value") for s in us if s["scenario_id"] == 15 and pred(s)
                       for x in (s.get("scenario_data") or [])
                       if isinstance(x, dict) and x.get("type") == 15001})
    print("scenario_data type=15001 取值：LF 段 %s｜scen15 全体 %s｜自己账号 %s"
          % (v15(lambda s: (s["rank_score"] or 0) >= LF_LINE), v15(lambda s: True),
             v15(lambda s: s["viewer"] == own_viewer)))

    diffs = [abs(s["diff_vs_official"]) for s in us if s.get("diff_vs_official") is not None]
    if diffs:
        diffs.sort()
        print("口径自检：派生分 vs 官方 rank_score 的 |差| 中位 = %.0f｜p90 = %.0f｜n=%d"
              % (diffs[len(diffs) // 2], diffs[int(len(diffs) * 0.9)], len(diffs)))

    edges = [(0, 30000, "<3万"), (30000, 50000, "3-5万"), (50000, 63627, "5万-靶"),
             (63627, 80000, "靶-8万"), (80000, LF_LINE, "8万-LF线"), (LF_LINE, 10 ** 9, "≥LF")]
    print("\n== 分数分布 ==")
    print("%-12s %10s %10s" % ("档", "终局快照", "自己的run(派生)"))
    ds = dist([s["rank_score"] for s in us], edges)
    dr = dist([r["score_end"] for r in runs], edges)
    for lo, hi, tag in edges:
        print("%-12s %10d %10d" % (tag, ds[tag], dr[tag]))

    print("\n== 自己的 run 逐回合覆盖（按派生终分降序，top %d）==" % top)
    print("%-24s %-6s %-6s %-6s %7s %7s %6s %6s %9s %9s %9s %s"
          % ("run", "scen", "route", "card", "t区间", "回合数", "缺", "五维和", "派生分", "官方终值", "PT", "mot"))
    for r in runs[:top]:
        gaps = [int(x) for x in r["gap_turns"].split(",") if x]
        print("%-24s %-6s %-6s %-6s %3s-%-4s %7d %6d %6s %9.0f %9s %9s %s" % (
            r["run"][:24], r["scen"], r["route_id"], r["card_id"], r["t_min"], r["t_max"],
            r["n_turns"], len(gaps), r["five_sum_end"], r["score_end"],
            r["official_rank_score"], r["skill_point_end"], r["motivation_end"]))

    # 关键问题：LF 段（≥min_score）有没有逐回合？
    hi_runs = [r for r in runs if r["score_end"] >= min_score]
    print("\n== ≥%d 的「自己的 run」= %d 条 ==" % (min_score, len(hi_runs)))
    for r in hi_runs:
        print("  run=%s 派生分=%.0f 覆盖 t%s–t%s 缺 %s" % (
            r["run"], r["score_end"], r["t_min"], r["t_max"],
            r["gap_turns"] or "无"))

    # t29–t48 / festa 窗口覆盖（所有 scenario15 的 run）
    print("\n== scenario15 的逐回合覆盖：t29–t48 与 festa/合宿窗口 ==")
    m15 = [r for r in runs if r["scen"] == 15]
    for r in m15:
        tset = {int(x) for x in r["turn_list"].split(",") if x}
        win = range(29, 49)
        have = sum(1 for t in win if t in tset)
        print("  run=%-22s t%s–t%s｜t29-48 命中 %2d/20｜合宿窗口 t37-44 命中 %2d/8｜缺 %s｜派生分 %.0f"
              % (r["run"], r["t_min"], r["t_max"], have, r["n_turns_37_44"],
                 ",".join(str(t) for t in win if t not in tset) or "无", r["score_end"]))

    # 「玩家要练哪个演目」到底能不能从报文直接读到
    print("\n== 演目意图字段 ==")
    st_vals, step_vals, gauge_full = collections.Counter(), collections.Counter(), 0
    n_snap = 0
    for s in cache["turns"].values():
        n_snap += 1
        step_vals[s.get("act_training_step")] += 1
        for at, v in (s.get("act_state") or {}).items():
            st_vals[v] += 1
        g = s.get("act_gauge") or {}
        nd = s.get("act_need") or {}
        if any(g.get(k) is not None and nd.get(k) is not None and g[k] >= nd[k] for k in g):
            gauge_full += 1
    print("逐回合快照 %d 条里 act_training_state 取值分布：%s" % (n_snap, dict(st_vals)))
    print("festa_info.act_training_step 取值分布：%s" % dict(sorted(step_vals.items(), key=lambda x: str(x[0]))))
    print("至少一个演目 gauge 满的快照 = %d/%d" % (gauge_full, n_snap))
    # act_training_step 与「五个演目的 state 组成」是否一一对应（1 个还是 2 个演目进训练态）
    by_step = collections.defaultdict(collections.Counter)
    for s in cache["turns"].values():
        c = collections.Counter((s.get("act_state") or {}).values())
        by_step[s.get("act_training_step")][(c[0], c[1], c[2])] += 1
    print("act_training_step vs (state0,state1,state2) 组成：")
    for step in sorted(by_step, key=str):
        tops = ", ".join("n0=%d n1=%d n2=%d:%d" % (k[0], k[1], k[2], v)
                         for k, v in by_step[step].most_common(6))
        print("   step=%-4s n=%-5d %s" % (step, sum(by_step[step].values()), tops))
    print("执行稽古(5101-5105)那一刻，**被练那个演目**的 act_training_state 分布：%s"
          % cache.get("act_state_of_keiko"))
    print("state 向量签名 (t1..t5 顺序，动作类别 | 签名) 取前 12：")
    for k, v in sorted(cache.get("act_state_sig", {}).items(), key=lambda x: -x[1])[:12]:
        print("   %-34s %d" % (k, v))
    print("局内请求体的全部键（看有没有 select_act / roadmap 一类）：")
    for k, v in sorted(cache.get("req_body_keys", {}).items()):
        print("   %-62s %d" % (k, v))
    print("请求体里名字含 act/select/target/plan/intent 的键（判「前置意图字段」的直接证据）：%s"
          % dict(cache.get("req_intent", {})))
    print("响应顶层 data.training_roadmap_selected_info 的形态（响应包 %d 份）：%s"
          % (sum((cache.get("roadmap_form") or {}).values()), cache.get("roadmap_form")))
    print("\n-- (b) 这一手是谁下的：exec_auto_play_plan_id（0=手动｜150101=自动育成方案）--")
    n_man = n_auto = 0
    for run in sorted(cache.get("plan_by_run", {})):
        c = cache["plan_by_run"][run]
        n_man += int(c.get("0", 0))
        n_auto += sum(v for k, v in c.items() if k not in ("0", "None"))
        print("   %-34s %s" % (run[:34], c))
    print("   合计 手动 %d／自动 %d" % (n_man, n_auto))
    print("-- (c) 玩家自己配的意图宣言 data.festa_data_set.auto_setting_info --")
    for run in sorted(cache.get("auto_decl_by_run", {})):
        print("   %-34s %s" % (run[:34], cache["auto_decl_by_run"][run]))
    print("   宣言矩阵内容（run|选中preset → 内容指纹种类，同内容合并计数）：")
    for k in sorted(cache.get("auto_matrix", {})):
        m = cache["auto_matrix"][k]
        print("      %-40s 种类=%d 总出现=%d" % (k[:40], len(m), sum(m.values())))
        sig = max(m, key=lambda s: m[s])
        print("        融合声明 advice_id←fusion_advice_id: %s" % json.dumps(json.loads(sig)[0]))
        for aid, acts in json.loads(sig)[1]:
            print("        id=%-3s %s" % (aid, json.dumps(acts)))
    return runs, us, lf


def dump(cache, runs, us, out_trace, out_board, out_index, out_snaps):
    """高分/全部局的逐回合 CSV + 局索引 + 终局快照表。"""
    keep = {r["run"] for r in runs}
    with open(out_trace, "w", newline="", encoding="utf-8-sig") as fh:
        cols = ["run", "viewer", "chara", "start_time", "turn", "scenario_id", "route_id",
                "card_id", "chara_grade", "rarity", "talent_level", "file",
                "speed", "stamina", "power", "guts", "wiz", "five_sum", "vital", "motivation",
                "skill_point", "fans", "sum_grade", "n_skills", "stat_score", "score",
                "max_speed", "max_stamina", "max_power", "max_guts", "max_wiz", "max_vital",
                "dmax_speed", "dmax_stamina", "dmax_power", "dmax_guts", "dmax_wiz",
                "camp_left", "max_act_level", "act_training_step", "act_lv", "act_gauge",
                "act_need", "act_state", "act_advice", "train_lv", "n_eval", "eval_sum",
                "n_cp", "race_program_id", "reserve_race_program_id", "playing_state", "state",
                "auto_call_preset_id", "auto_decl_n", "auto_decl"]
        w = csv.writer(fh)
        w.writerow(cols)
        st_rows = sorted(cache["turns"].values(), key=lambda s: (s["run"], s["turn"]))
        for s in st_rows:
            if s["run"] not in keep:
                continue
            w.writerow([json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                        for v in (s.get(c) for c in cols)])
    brows = [b for b in cache["board"] if b["run"] in keep]
    with open(out_board, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(brows[0].keys()) + ["five_sum_preview"])
        w.writeheader()
        for b in brows:
            b = dict(b)
            b["five_sum_preview"] = sum(b.get(a) or 0 for a in AXES)
            w.writerow(b)
    with open(out_index, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(runs[0].keys()))
        w.writeheader()
        w.writerows(runs)
    print("\n逐回合 %d 行 → %s" % (len({(s['run'], s['turn']) for s in cache['turns'].values()}), out_trace))
    print("板上报文 %d 行 → %s" % (len(brows), out_board))
    print("局索引 %d 行 → %s" % (len(runs), out_index))
    snap_path = out_snaps
    with open(snap_path, "w", newline="", encoding="utf-8-sig") as fh:
        cols = [c for c in next(iter(us), {}).keys() if c != "scenario_data"]
        w = csv.DictWriter(fh, fieldnames=cols + ["n_seen", "seen_files", "five"])
        w.writeheader()
        for s in us:
            r = {k: s.get(k) for k in cols}
            r["n_seen"] = s.get("n_seen")
            r["seen_files"] = ";".join(s.get("files") or [])
            r["five"] = json.dumps([s.get(a) for a in AXES], ensure_ascii=False)
            w.writerow(r)
    print("终局快照 %d 张 → %s" % (len(us), snap_path))
    return snap_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=LF_LINE, help="高分判定线（默认 LF 起线 91400）")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--limit", type=int, default=0, help="只扫前 N 个报文（冒烟用，0=全量）")
    ap.add_argument("--cache", default=CACHE, help="扫描缓存路径")
    ap.add_argument("--from-cache", action="store_true", help="复用缓存，不重扫报文")
    ap.add_argument("--trace", default="out/lf_turn_trace_20261104.csv")
    ap.add_argument("--board", default="out/lf_board_preview_20261104.csv")
    ap.add_argument("--index", default="out/lf_run_index_20261104.csv")
    ap.add_argument("--snaps", default="out/lf_chara_snapshots_20261104.csv")
    a = ap.parse_args()
    os.makedirs("out", exist_ok=True)
    if a.from_cache and os.path.exists(a.cache):
        cache = json.load(open(a.cache, encoding="utf-8"))
        print("读缓存 %s（未重扫报文；缓存里 files=%s）" % (a.cache, cache["stats"].get("files")))
    else:
        t0 = time.time()
        cache = scan(a.limit)
        cache["meta"] = {"scan_seconds": round(time.time() - t0, 1), "limit": a.limit,
                         "raw_dir": RAW, "k_scen15": K_SCEN15, "lf_line": LF_LINE}
        json.dump(cache, open(a.cache, "w", encoding="utf-8"), ensure_ascii=False)
        print("扫描用时 %.0fs，缓存写入 %s" % (time.time() - t0, a.cache))
    runs, us, lf = report(cache, a.min_score, a.top)
    dump(cache, runs, us, a.trace, a.board, a.index, a.snaps)


if __name__ == "__main__":
    main()
