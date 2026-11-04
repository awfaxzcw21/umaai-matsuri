"""分身（`sub_command_partner_array`）可见率的决定变量（#51，= #47 §7-2 / §7-3）。

一条命令重建全部表：

    python tools/clone_visibility.py            # 全量报文扫描（约 2 分钟）
    python tools/clone_visibility.py --card-csv out/clone_partners_20261104.csv

只做测量，不改任何 tracked 文件、不写 `data/`、不接引擎、不造常数。
链路复用 #47 的 `tools/clone_partner_ids.py`（`read_board` / `card_state` / `load_tables`），
本脚本只补两件它没有的东西：**决策时点那块板** 和 **run 级候选变量**。

先写死、再跑的口径（禁止事后调分层边界）：

1. **决策板**＝同一 `(run,turn)` 里**最后一条 `chara_info.playing_state==1` 的板报文**
   （玩家做选择时看到的那块板）。没有 ps==1 的板报文才回退到最后一条。
   #47 用的是「最后一条报文」，那一条常常是 `ps=5` 的结算包，板子已被服务端重洗。
2. **可见**＝该 `(run,turn,卡)` 上 `len(sub_command_partner_array 命中该站位) >= 1`；
   主单元 = **带 801 协助教诲、且该卡在决策板上确有站位** 的卡回合。
3. **完全分开**＝某候选变量的取值集合在「0 槽组」与「≥1 槽组」**交集为空且 0 例外**；
   3b **高基数变量不参与该判定**（回合号、计数、id、金额这类逐回合都不同的量，两组各取一个
   独值就能"分开"，那不是机制；见 `HIGHCARD`），3c 两组样本各 ≥3 才允许下结论。
   否则记为「解释不了」，只报分层可见率（逐层 n 与比例），不称作机制。
4. 数值型候选按**先写死的粗档**分层（体力 0 / 1–30 / 31–60 / 61–107 / 108；动机 1–5 原值），
   不许拿分档去凑分离。
5. 两个特征的交叉只允许在「单特征已把样本切成两块」之后各查一次，且仍要求 0 例外才登记。
6. 「当回合是否比赛 / 做了什么」是任务书点名的候选，两侧都取：响应侧 `chara_info.race_program_id`
   （`t_race`，该回合任一包非 0）＋请求侧 exec 命令（`t_train` / `t_rest` / `t_exec_any`，
   按请求体 `current_turn` 归合）；`t_train_win`（按报文序号归合）只用作两者的交叉自检，
   不一致的回合数在 §C 登记。
7. 为排除「不可见只是决策板取错」这一可能，每张卡另记一个 `n_slot_any_ps1`＝同一 `(run,turn)`
   **所有** `ps==1` 板上该卡分身槽数的最大值；§J 比较两个口径的例外集是否同一批。

"""

import argparse
import collections
import csv
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402
import clone_partner_ids as cpi  # noqa: E402

RAW = cpi.RAW
MDB = cpi.MDB
OUT_TURN = "out/_clone_visibility_20261104.csv"
OUT_CARD = "out/_clone_visibility_card_20261104.csv"
OUT_TXT = "out/_clone_visibility_report_20261104.txt"
SCEN = 15

# 候选变量登记表：显示名 -> (报文里的真实路径, 取值方式)
# 取值方式里 turn 级聚合用 "any/first/last"，first|last 都指决策板包。
TURN_FEATS = [
    ("t_race", "派生：该 (run,turn) 任一包 chara_info.race_program_id != 0（口径见 --help）"),
    ("t_train", "派生：请求体 current_turn==本回合 且 command_type==1（当回合确实练了）"),
    ("t_rest", "派生：同上 command_type==3"),
    ("t_exec_any", "派生：同上任意 command_type"),
    ("t_train_win", "派生：exec 请求报文序号落在本回合窗口且 command_type==1（与 t_train 的交叉自检）"),
    ("exec_win_turn_diff", "口径自检：t_train 与 t_train_win 的非空状态是否不同"),
    ("vital", "chara_info.vital"),
    ("max_vital", "chara_info.max_vital"),
    ("motivation", "chara_info.motivation"),
    ("chara_state", "chara_info.state"),
    ("dec_ps", "chara_info.playing_state @决策板"),
    ("turn_max_ps", "max(chara_info.playing_state) 全回合包"),
    ("rpi_dec", "chara_info.race_program_id @决策板"),
    ("rpi_any", "chara_info.race_program_id 全回合包(去重,0除外)"),
    ("reserve_rpi", "chara_info.reserve_race_program_id @决策板"),
    ("race_start", "'race_start_info' in data（全回合任一包）"),
    ("race_history", "'race_history' in data（全回合任一包）"),
    ("race_reward", "'race_reward_info' in data（全回合任一包）"),
    ("race_req_n", "本回合窗口内 single_mode_race_*_request 包数"),
    ("exec_ids", "single_mode_exec_command_request_common.command_id（本回合）"),
    ("exec_types", "single_mode_exec_command_request_common.command_type（本回合）"),
    ("continue_req", "'single_mode_continue_request_common'（本回合窗口）"),
    ("fans", "chara_info.fans @决策板"),
    ("skill_point", "chara_info.skill_point @决策板"),
    ("short_cut_state", "chara_info.short_cut_state @决策板"),
    ("is_short_race", "chara_info.is_short_race @决策板"),
    ("is_non_short_cut_unchecked_event", "chara_info.is_non_short_cut_unchecked_event @决策板"),
    ("unchecked_event_n", "len(data.unchecked_event_array) @决策板"),
    ("guest_outing_n", "len(chara_info.guest_outing_info_array) @决策板"),
    ("skill_tips_n", "len(chara_info.skill_tips_array) @决策板"),
    ("race_condition_n", "len(data.race_condition_array) @决策板"),
    ("route_race_n", "len(chara_info.route_race_id_array) @决策板"),
    ("tlv_sum", "sum(training_level_info_array[].training_level) @决策板"),
    ("special_home", "home_info.special_home_id @决策板"),
    ("training_bonus_remain", "home_info.training_bonus_remain_turn_num @决策板"),
    ("race_entry_restriction", "home_info.race_entry_restriction @决策板"),
    ("shortened_race_state", "home_info.shortened_race_state @决策板"),
    ("disable_cmd_n", "len(home_info.disable_command_id_array) @决策板"),
    ("disable_cmd", "home_info.disable_command_id_array @决策板(排序元组)"),
    ("disable_race_turn_n", "len(home_info.disable_race_turn_array) @决策板"),
    ("available_continue", "home_info.available_continue_num @决策板"),
    ("available_free_continue", "home_info.available_free_continue_num @决策板"),
    ("free_continue_num", "home_info.free_continue_num @决策板"),
    ("continue_unlock_flag", "home_info.continue_unlock_flag @决策板"),
    ("camp_remain", "festa_data_set.festa_info.summer_camp_remain_turn_num @决策板"),
    ("max_act_level", "festa_data_set.festa_info.max_act_level @决策板"),
    ("act_training_step", "festa_data_set.festa_info.act_training_step @决策板"),
    ("check_point_n", "len(festa_data_set.check_point_result_array) @决策板"),
    ("n_entry", "决策板上 home_info.command_info_array 里 10x/510x 条目数"),
    ("n_keiko", "决策板上 510x 条目数"),
    ("n_plain", "决策板上 10x 条目数"),
    ("heads_sum", "sum(len(training_partner_array)) @决策板"),
    ("npc_sum", "sum(站位>=100 的人头) @决策板"),
    ("sub_sum", "sum(len(sub_command_partner_array)) @决策板"),
    ("pkt_n", "本 (run,turn) 板报文条数"),
    ("turn", "chara_info.turn"),
]
# 表侧 `single_mode_turn`（turn_set_id=15）同回合属性：#47 §7-3 说这批回合一字不差，
# 这里把全部列都取下来，让「同不同」由数据说，而不是引用旧结论。
TBL_COLS = ["year", "month", "half", "period", "unique_command", "training_set_id",
            "outing_set_id", "rest_type", "health_room_type", "race_entry_type",
            "top_cloth_id", "top_bg_id", "story_cloth_id", "story_bg_id", "story_bg_sub_id"]


# 口径 3b（先写死）：这些变量逐回合几乎都取不同值（计数 / id / 金额 / 回合号本身），
# 用它判「完全分开」必然成功、必然没意义 ⇒ 只进 §F 分层，不参与 §E 的分开判定。
HIGHCARD = {"turn", "vital", "max_vital", "fans", "skill_point", "rpi_any", "rpi_dec",
            "reserve_rpi", "disable_cmd", "disable_cmd_n", "disable_race_turn_n",
            "exec_ids", "heads_sum", "npc_sum", "sub_sum", "tlv_sum", "own_act_lv",
            "own_act_gauge", "own_act_need", "pkt_n", "race_req_n", "dec_file",
            "unchecked_event_n", "skill_tips_n", "race_condition_n", "route_race_n",
            "guest_outing_n", "check_point_n", "n_entry", "n_keiko", "n_plain",
            "body_cid", "body_type", "card_id", "pos"}


def turn_table(mdb):
    c = sqlite3.connect("file:%s?mode=ro" % mdb.replace("\\", "/"), uri=True)
    cur = c.execute("select * from single_mode_turn where turn_set_id=%d" % SCEN)
    cols = [d[0] for d in cur.description]
    out = {}
    for r in cur.fetchall():
        d = dict(zip(cols, r))
        out[d["turn"]] = d
    c.close()
    return out


def packet_board(data, chara):
    """响应包 -> (板快照, chara_info)，只有确实带训练条目的板才算。"""
    hi = data.get("home_info")
    if not isinstance(hi, dict) or not hi.get("command_info_array"):
        return None
    b = cpi.read_board(data, chara)
    if not isinstance(b["turn"], int) or not b["entry"]:
        return None
    return b


def arr_len(x):
    return len(x) if isinstance(x, (list, tuple)) else 0


def walk(args, chara):
    """按文件序过一遍报文：板包按 (run,turn) 归堆，请求包按全局序号归到回合窗口。"""
    pk = collections.defaultdict(list)
    acts = collections.defaultdict(list)      # run -> [(idx, turn, type, id)]
    req_idx = collections.defaultdict(list)   # run -> [(idx, kind)]  race/continue 请求
    cur = [None]
    keys = collections.Counter()              # 板包字段出现频次（登记真实路径用）
    names = collections.Counter()
    idx = 0
    for f in sorted(os.listdir(args.raw)):
        idx += 1
        if not f.endswith(".bin"):
            continue
        try:
            d = ml.decode_file(args.raw + f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if f.endswith("_notify_request.bin"):
            body = d.get("single_mode_exec_command_request_common")
            if isinstance(body, dict):
                acts[cur[0]].append((idx, body.get("current_turn"),
                                     body.get("command_type"), body.get("command_id")))
            for k in d:
                if isinstance(k, str) and k.endswith("_request_common"):
                    names[k] += 1
                    if isinstance(d[k], dict):
                        for bk in d[k]:
                            names["%s.%s" % (k, bk)] += 1
                    if "race_" in k or k == "single_mode_continue_request_common":
                        req_idx[cur[0]].append((idx, k))
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or ci.get("scenario_id") != SCEN:
            continue
        r = ci.get("single_mode_chara_id")
        cur[0] = r
        b = packet_board(data, chara)
        if b is None:
            continue
        for k in ci:
            keys["chara_info.%s" % k] += 1
        for k in (data.get("home_info") or {}):
            keys["home_info.%s" % k] += 1
        fds = data.get("festa_data_set") or {}
        for k in fds:
            keys["festa_data_set.%s" % k] += 1
        for k in (fds.get("festa_info") or {}):
            keys["festa_data_set.festa_info.%s" % k] += 1
        for k in data:
            keys["data.%s" % k] += 1
        pk[(r, b["turn"])].append((idx, f, ci.get("playing_state"), b, data, ci))
    return pk, acts, req_idx, dict(keys), dict(names)


def decision_pack(v):
    """口径 1：最后一条 ps==1 的板报文（优先没有 command_result 的那条）。"""
    ps1 = [x for x in v if x[2] == 1]
    cand = [x for x in ps1 if not isinstance(x[4].get("command_result"), dict)] or ps1 or v
    return cand[-1]


def feat_of(rec, turn, v, acts_r, req_r, args):
    """一条 (run,turn) 的全部候选变量。"""
    idx, f, ps, b, data, ci = rec
    lo = min(x[0] for x in v)
    hi = max(x[0] for x in v)
    inwin = [k for (i, k) in req_r if lo <= i <= hi]
    rpi_any = sorted({x[5].get("race_program_id") for x in v} - {0, None})
    home = data.get("home_info") or {}
    fdata = data.get("festa_data_set") or {}
    tlv = [e for e in (ci.get("training_level_info_array") or []) if isinstance(e, dict)]
    lvl = sum((e.get("training_level") or 0) for e in tlv)
    heads = npc = sub = nk = np = 0
    for cid, c in b["entry"].items():
        p = [q for q in (c.get("training_partner_array") or []) if isinstance(q, int)]
        heads += len(p)
        npc += sum(1 for q in p if q >= 100)
        sub += sum(1 for q in (c.get("sub_command_partner_array") or []) if isinstance(q, int))
        if cid in cpi.KEIKO:
            nk += 1
        elif cid in cpi.PLAIN:
            np += 1
    hi_tbl = args.tbl.get(turn) or {}
    dis = sorted(str(x) for x in (home.get("disable_command_id_array") or []))
    ex = [a for a in acts_r if a[1] == turn]                 # 请求体里 current_turn==本回合
    ex_win = [a for a in acts_r if lo <= a[0] <= hi]          # 报文序号落在本回合窗口
    d = {
        "vital": ci.get("vital"), "max_vital": ci.get("max_vital"),
        "motivation": ci.get("motivation"), "chara_state": ci.get("state"),
        "dec_ps": ps, "turn_max_ps": max(x[2] for x in v if isinstance(x[2], int)) if v else None,
        "rpi_dec": ci.get("race_program_id"),
        "rpi_any": "|".join(str(x) for x in rpi_any),
        "reserve_rpi": ci.get("reserve_race_program_id"),
        "race_start": int(any("race_start_info" in x[4] for x in v)),
        "race_history": int(any("race_history" in x[4] for x in v)),
        "race_reward": int(any("race_reward_info" in x[4] for x in v)),
        "race_req_n": sum(1 for k in inwin if "race_" in k),
        "exec_ids": "|".join(str(a[3]) for a in ex),
        "exec_types": "|".join(str(a[2]) for a in ex),
        "t_train": int(any(a[2] == 1 for a in ex)),
        "t_rest": int(any(a[2] == 3 for a in ex)),
        "t_exec_any": int(bool(ex)),
        "t_train_win": int(any(a[2] == 1 for a in ex_win)),
        "exec_win_turn_diff": int(bool(ex) != bool(ex_win)),
        "continue_req": int(any(k == "single_mode_continue_request_common" for k in inwin)),
        "fans": ci.get("fans"), "skill_point": ci.get("skill_point"),
        "short_cut_state": ci.get("short_cut_state"),
        "is_short_race": ci.get("is_short_race"),
        "is_non_short_cut_unchecked_event": ci.get("is_non_short_cut_unchecked_event"),
        "unchecked_event_n": arr_len(data.get("unchecked_event_array")),
        "guest_outing_n": arr_len(ci.get("guest_outing_info_array")),
        "skill_tips_n": arr_len(ci.get("skill_tips_array")),
        "race_condition_n": arr_len(data.get("race_condition_array")),
        "route_race_n": arr_len(ci.get("route_race_id_array")),
        "tlv_sum": lvl,
        "special_home": home.get("special_home_id"),
        "training_bonus_remain": home.get("training_bonus_remain_turn_num"),
        "race_entry_restriction": home.get("race_entry_restriction"),
        "shortened_race_state": home.get("shortened_race_state"),
        "disable_cmd_n": len(dis), "disable_cmd": "|".join(dis),
        "disable_race_turn_n": arr_len(home.get("disable_race_turn_array")),
        "available_continue": home.get("available_continue_num"),
        "available_free_continue": home.get("available_free_continue_num"),
        "free_continue_num": home.get("free_continue_num"),
        "continue_unlock_flag": home.get("continue_unlock_flag"),
        "camp_remain": (fdata.get("festa_info") or {}).get("summer_camp_remain_turn_num"),
        "camp": b["camp"],
        "max_act_level": (fdata.get("festa_info") or {}).get("max_act_level"),
        "act_training_step": b["step"],
        "check_point_n": arr_len(fdata.get("check_point_result_array")),
        "n_entry": len(b["entry"]), "n_keiko": nk, "n_plain": np,
        "heads_sum": heads, "npc_sum": npc, "sub_sum": sub, "pkt_n": len(v), "turn": turn,
    }
    for col in TBL_COLS:
        d["t_" + col] = hi_tbl.get(col)
    return d


def card_rows(pk, tbl, args):
    """(run,turn) 决策板 × 卡组里带 +1 来源的卡 -> 可见与否 + 该卡自身状态。"""
    deyi, stype, chara, a2cmd, base, fin = tbl
    turns = {}
    for (r, t), v in sorted(pk.items()):
        turns[(r, t)] = decision_pack(v)
    out = []
    for (r, t), rec in sorted(turns.items(), key=lambda x: (x[0][0], x[0][1])):
        b = rec[3]
        feats = feat_of(rec, t, v=pk[(r, t)], acts_r=args.acts.get(r, []),
                        req_r=args.req.get(r, []), args=args)
        # 口径自检：同一 (run,turn) 里*每一块* ps==1 板都给这张卡的分身槽数取最大值
        # （若决策板=0 而别的板>0，那是「取哪块板」的口径问题，不是机制）
        alt = {}
        for x in pk[(r, t)]:
            if x[2] != 1:
                continue
            for pos in x[3]["deck"]:
                if not (isinstance(pos, int) and pos < 100):
                    continue
                k2 = cpi.card_state(x[3], pos, tbl)
                if k2["ctype"] == 1:
                    alt[k2["card"]] = max(alt.get(k2["card"], 0), len(k2["mk"]))
        srcs = []
        for pos in b["deck"]:
            if not (isinstance(pos, int) and pos < 100):
                continue
            k = cpi.card_state(b, pos, tbl)
            if k["ctype"] != 1:
                continue
            src = "801" if k["src801"] else ("bond" if k["srcbond"] else
                                             ("fin" if k["src_fin"] else ""))
            if not src:
                continue
            srcs.append((pos, src, k))
        for pos, src, k in srcs:
            own_keiko = a2cmd.get(k["act"])
            row = dict(feats)
            row.update({
                "run": r, "src": src, "pos": pos, "card_id": k["card"],
                "vis": 1 if k["mk"] else 0, "n_slot": len(k["mk"]),
                "n_slot_any_ps1": alt.get(k["card"], 0),
                "on_board": k["on_board"],
                "body_cid": "|".join(str(x) for x in sorted(k["body"])),
                "body_type": "|".join(str(base.get(x, x)) for x in sorted(k["body"])),
                "own_act": k["act"], "own_keiko": own_keiko,
                "own_keiko_on_board": int(own_keiko in b["entry"]),
                "own_act_lit": k["lit"], "own_act_state": k["state"],
                "own_act_gauge": k["gauge"], "own_act_need": k["need"], "own_act_lv": k["act_lv"],
                "advice_obj": k["adv_obj"], "advice_base": k["adv_base"],
                "in_finals": int(t in fin),
                "src801": k["src801"], "srcbond": k["srcbond"], "srcfin": k["src_fin"],
                "n_src": k["src_adv"] + k["src_fin"],
            })
            out.append(row)
    return out, turns


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "1" if v else "0"
    s = str(v)
    return s if len(s) <= 28 else s[:28] + "…"


def separability(rows):
    """口径 3：候选变量的两组取值集合交集为空，才算「完全分开」。

    另加两条先写死的防呆：口径 3b 高基数变量（见 HIGHCARD）不参与判定；
    口径 3c 两组样本各 >= 3 才允许下「分开」的结论。
    """
    a = [x for x in rows if x["vis"] == 0]
    b = [x for x in rows if x["vis"] == 1]
    extra = [("own_keiko_on_board", "决策板上是否有该卡担当演目的 510x 条目"),
             ("own_act_lit", "act_info_array[担当演目].act_gauge>=need_act_gauge"),
             ("own_act_state", "act_info_array[担当演目].act_training_state"),
             ("own_act_lv", "act_info_array[担当演目].level"),
             ("own_act_gauge", "act_info_array[担当演目].act_gauge"),
             ("own_act_need", "act_info_array[担当演目].need_act_gauge"),
             ("heads_bucket", "决策板 Σheads（先写死档）"),
             ("vital_bucket", "chara_info.vital（先写死档）")]
    res = []
    for name, path in TURN_FEATS + extra:
        if name == "heads_bucket":
            def g(x):
                v = x["heads_sum"]
                return "0" if v == 0 else "1-5" if v <= 5 else "6-10" if v <= 10 else "11+"
        elif name == "vital_bucket":
            def g(x):
                return vital_bucket(x["vital"])
        else:
            def g(x, n=name):
                return x.get(n)
        va = collections.Counter(g(x) for x in a)
        vb = collections.Counter(g(x) for x in b)
        if not (va and vb):
            continue
        ov = set(va) & set(vb)
        hi = name in HIGHCARD or len(va) + len(vb) > 8
        thin = len(a) < 3 or len(b) < 3
        if ov:
            verdict = "否"
        elif hi or thin:
            verdict = "不算（高基数/样本太薄）"
        else:
            verdict = "是（0 例外）"
        # 必要条件（单侧）：存在某个取值 v，使「不可见」全部落在 v 上
        # （即 v 覆盖了 a 的全部样本，同时 b 里还有别处也成立 ⇒ 不是完全分离，但是一条 0 例外的下界）
        nec = ""
        if not hi and len(a) >= 3:
            for v, n in va.items():
                if n == len(a) and sum(vb.values()) - vb.get(v, 0) >= 3:
                    nec = "必要：%s（不可见 %d/%d 全在此档，另有 %d 个可见样本不在此档）" % (
                        fmt(v), n, len(a), sum(vb.values()) - vb[v])
                    break
        res.append((name, path, len(va), len(vb), len(ov), verdict, nec))
    return res


def vital_bucket(v):
    """口径 4：档位先写死，不看分离效果再改。"""
    if not isinstance(v, int):
        return "?"
    if v == 0:
        return "0"
    if v <= 30:
        return "1-30"
    if v <= 60:
        return "31-60"
    if v <= 107:
        return "61-107"
    return "108"


def report(rows, turns, keys, names, tbl, args, fh):
    deyi, stype, chara, a2cmd, base, fin = tbl
    p = lambda s: print(s, file=fh)  # noqa: E731
    p("# 分身可见率的决定变量（#51）留档  2026-11-04")
    p("复现：python tools/clone_visibility.py")
    p("单元：(run,turn,带来源的卡)｜决策板＝该 (run,turn) 最后一条 playing_state==1 的板报文")

    p("\n## A. 板报文里真实存在的字段（路径 × 出现包数）——候选变量只能从这里取")
    for grp in ("chara_info", "home_info", "festa_data_set", "festa_data_set.festa_info", "data"):
        ks = sorted(k for k in keys if k.startswith(grp + "."))
        p("  %s："% grp)
        for k in ks:
            p("     %-58s %d" % (k, keys[k]))
    n_board_pkt = max(keys.get("chara_info.turn", 0), 1)
    p("  板报文条数（同一 (run,turn) 会有多条）：%d" % n_board_pkt)
    pat = re.compile(r"clone|visib|visible|sub_partner|extra_partner|appear|hide|switch", re.I)
    hit = [k for k in keys if pat.search(k.rsplit(".", 1)[-1])]
    p("  按「分身 / 可见 / 出现 / 开关」类命名扫全部板包字段名：命中 %d 个 %s"
      % (len(hit), hit or "（无，报文里没有现成的可见性开关字段）"))

    p("\n## B. 任务点名的候选变量：逐条核「报文里到底有没有这个字段」（按 A 的实测频次）")
    want = [("体力/气力", ["chara_info.vital", "chara_info.max_vital"]),
            ("动机", ["chara_info.motivation", "chara_info.state"]),
            ("当回合是否比赛（响应侧）",
             ["chara_info.race_program_id", "chara_info.reserve_race_program_id",
              "chara_info.race_running_style", "data.race_start_info", "data.race_history",
              "data.race_reward_info", "data.race_condition_array",
              "data.race_add_reward_info", "data.win_saddle_id_array",
              "home_info.race_entry_restriction", "home_info.shortened_race_state",
              "home_info.disable_race_turn_array"]),
            ("当回合做了什么（请求侧）",
             ["single_mode_exec_command_request_common", "single_mode_race_start_request_common",
              "single_mode_race_entry_request_common", "single_mode_race_out_request_common",
              "single_mode_race_end_request_common", "single_mode_check_event_request_common",
              "single_mode_continue_request_common",
              "single_mode_multi_race_reserve_request_common"]),
            ("当回合是否外出", ["chara_info.guest_outing_info_array"]),
            ("当回合是否休息", ["chara_info.rest_type", "home_info.rest_type",
                              "single_mode_exec_command_request_common.command_type"]),
            ("事件/story 标记", ["data.unchecked_event_array", "event_effected_factor_array",
                              "chara_info.is_non_short_cut_unchecked_event",
                              "chara_info.short_cut_state"]),
            ("演目状态", ["festa_data_set.act_info_array", "festa_data_set.festa_info",
                       "festa_data_set.command_info_array"]),
            ("合宿剩余", ["festa_data_set.festa_info.summer_camp_remain_turn_num"]),
            ("末段/决赛开关", ["festa_data_set.festa_info.final_phase",
                          "festa_data_set.festa_info.ura_final",
                          "data.festa_data_set.training_roadmap_selected_info"]),
            ("分身可见开关本身", ["chara_info.clone_visible", "home_info.sub_partner_enable",
                            "festa_data_set.festa_info.extra_partner"])]
    allk = dict(keys)
    allk.update(names)
    for lab, cands in want:
        found = [(k, allk[k]) for k in cands if k in allk]
        miss = [k for k in cands if k not in allk]
        p("  %-24s 找到：%s" % (lab, ", ".join("%s(%d包)" % (k, n) for k, n in found) or "无"))
        p("  %-24s 没有的候选路径：%s" % ("", ", ".join(miss) or "（全部命中）"))

    p("\n## C. 决策板口径下的样本量")
    p("  (run,turn) 唯一板 %d 个｜决策板为 ps==1 的 %d 个｜决策板回退到其它 ps 的 %d 个" % (
        len(turns), sum(1 for x in turns.values() if x[2] == 1),
        sum(1 for x in turns.values() if x[2] != 1)))
    p("  同一 (run,turn) 内 sub 标记数会变的板数（用决策板前必须知道的口径风险）：%d" %
      sum(1 for (r, t), v in args.pk.items()
          if len({sum(len(c.get("sub_command_partner_array") or [])
                      for c in x[3]["entry"].values()) for x in v}) > 1))
    bysrc = collections.Counter((x["src"], x["on_board"], x["vis"]) for x in rows)
    p("  (来源, 卡是否已在板上, 可见) →")
    for k in sorted(bysrc, key=str):
        p("     %-28s %d" % (k, bysrc[k]))
    tr = getattr(args, "trows", [])
    if tr:
        p("  回合级：比赛回合 %d／训练回合(有 exec type=1) %d／既没比赛也没训练 %d" % (
            sum(1 for x in tr if x["t_race"]), sum(1 for x in tr if x["t_train"]),
            sum(1 for x in tr if not x["t_race"] and not x["t_train"])))
        p("  口径 6 自检：按 current_turn 归合的 t_train 与按报文窗口归合的 t_train_win "
          "非空状态不同的回合数：%d" % sum(1 for x in tr if x["exec_win_turn_diff"]))

    p("\n## D. #47 §7-3 主问题：801 协助卡在板上时，什么决定它出不出分身")
    uni = [x for x in rows if x["src"] == "801" and x["on_board"]]
    off = [x for x in rows if x["src"] == "801" and not x["on_board"]]
    p("  主单元（801 卡 + 确有站位）%d 卡回合｜不可见 %d｜另：801 卡当回合根本不在板上 %d（板没铺它，"
      "与可见率无关）" % (len(uni), sum(1 for x in uni if not x["vis"]), len(off)))
    for lab, sel in (("全部", lambda x: True),
                     ("非比赛回合", lambda x: not x["t_race"]),
                     ("比赛回合", lambda x: bool(x["t_race"])),
                     ("当回合练了(t_train=1)", lambda x: x["t_train"]),
                     ("当回合没练(t_train=0)", lambda x: not x["t_train"])):
        g = [x for x in uni if sel(x)]
        p("  %-10s 卡回合%5d  可见%5d  可见率%7.2f%%  2槽%4d" % (
            lab, len(g), sum(x["vis"] for x in g),
            100.0 * sum(x["vis"] for x in g) / max(len(g), 1),
            sum(1 for x in g if x["n_slot"] >= 2)))
    p("  按 run × 比赛/非比赛（每格：可见/卡回合）：")
    grid = collections.defaultdict(lambda: [0, 0, 0, 0])
    for x in uni:
        c = grid[x["run"]]
        if x["t_race"]:
            c[2] += 1
            c[3] += x["vis"]
        else:
            c[0] += 1
            c[1] += x["vis"]
    p("     run    非比赛可见/总   比赛可见/总")
    for r in sorted(grid):
        c = grid[r]
        p("     %-6s %4d/%-4d        %4d/%-4d" % (r, c[1], c[0], c[3], c[2]))

    p("  来源数 × 实际槽数（#47 §3 的「槽数 ≤ 来源数」在决策板口径下的重算；行=全部带来源卡回合）：")
    for lab, sel in (("全部", lambda x: True),
                     ("非比赛回合", lambda x: not x["t_race"]),
                     ("比赛回合", lambda x: bool(x["t_race"]))):
        g = [x for x in rows if sel(x)]
        cc = collections.Counter((x["n_src"], x["n_slot"]) for x in g)
        p("     %-10s n=%-5d %s" % (lab, len(g), dict(sorted(cc.items()))))
        p("     %-10s 槽>来源 %d｜槽<来源(且来源在位) %d" % (
            "", sum(v for (s, n), v in cc.items() if n > s),
            sum(v for (s, n), v in cc.items() if n < s)))

    p("\n## E. 完全分离检验（口径 3：两组取值集合交集为空才算分开）")
    p("  「判定」列只认完全分离；最右列是另一种 0 例外——必要条件（不可见全落在同一档）")
    p("  %-34s %-6s %-6s %-8s %-26s %s" % ("候选变量", "0槽档", "1槽档", "交集档", "判定", "出处"))
    res = separability(uni)
    for name, path, na, nb, nov, verdict, nec in sorted(
            res, key=lambda x: (x[5] != "是（0 例外）", x[4])):
        p("  %-34s %-6d %-6d %-8d %-26s [%s]" % (name, na, nb, nov, verdict, path))
        if nec:
            p("      ↳ 单侧 0 例外：%s" % nec)

    p("\n## E2. 层内的完全分离检验（口径 5：t_race 已把样本切成两块，各查一次）")
    for lab, sel in (("比赛回合层", lambda x: bool(x["t_race"])),
                     ("非比赛回合层", lambda x: not x["t_race"])):
        ur = [x for x in uni if sel(x)]
        nv = sum(x["vis"] for x in ur)
        nz = len(ur) - nv
        p("  %s：%d 卡回合（可见 %d／不可见 %d）" % (lab, len(ur), nv, nz))
        if not (nv and nz):
            p("     层内两组里有一组为空 ⇒ 可见性在这一层恒定，不需要再分离")
            continue
        p("  %-34s %-6s %-6s %-8s %-26s %s" % (
            "候选变量", "0槽档", "1槽档", "交集档", "判定", "出处"))
        for name, path, na, nb, nov, verdict, nec in sorted(
                separability(ur), key=lambda x: (x[5] != "是（0 例外）", x[4])):
            p("  %-34s %-6d %-6d %-8d %-26s [%s]" % (
                name, na, nb, nov, verdict, path))
            if nec:
                p("      ↳ 单侧 0 例外：%s" % nec)

    p("\n## F. 分层可见率（分不开时唯一能给的东西；档位先写死）")
    for lab, g in (("当回合是否比赛", lambda x: x["t_race"]),
                   ("当回合是否练了", lambda x: x["t_train"]),
                   ("比赛|练了", lambda x: "%s|%s" % (x["t_race"], x["t_train"])),
                   ("动机 chara_info.motivation", lambda x: x["motivation"]),
                   ("体力档", lambda x: vital_bucket(x["vital"])),
                   ("决策板 Σheads 档", lambda x: ("0" if x["heads_sum"] == 0 else
                                                   "1-5" if x["heads_sum"] <= 5 else
                                                   "6-10" if x["heads_sum"] <= 10 else "11+")),
                   ("担当演目彩圈", lambda x: x["own_act_lit"]),
                   ("担当演目 act_training_state", lambda x: x["own_act_state"]),
                   ("该演目 510x 是否在板", lambda x: x["own_keiko_on_board"]),
                   ("比赛×该演目510x在板", lambda x: "%s|%s" % (x["t_race"], x["own_keiko_on_board"]))):
        c = collections.defaultdict(lambda: [0, 0])
        for x in uni:
            k = str(g(x))
            c[k][0] += 1
            c[k][1] += x["vis"]
        p("  %s：" % lab)
        for k in sorted(c, key=str):
            p("     %-12s n=%-5d 可见率 %6.2f%%" % (k, c[k][0], 100.0 * c[k][1] / c[k][0]))

    p("\n## G. #47 §7-2 末段第 2 槽的奇偶能不能和 race_entry_type 分开")
    p("  表侧 single_mode_turn(turn_set_id=15) 的 race_entry_type==0 的回合：%s" %
      sorted(t for t, d in args.tbl.items() if d.get("race_entry_type") == 0))
    tr = getattr(args, "trows", [])
    p("  报文侧「当回合出现比赛」的 (run,turn)：%d / %d（口径 6：任一包 race_program_id≠0）" % (
        sum(1 for x in tr if x["t_race"]), len(tr)))
    p("  全量 (run,turn)：entry==1 但当回合没比赛 %d｜entry==0 但当回合有比赛 %d"
      "（⇒ entry 字段能不能当「这回合比赛」用）" % (
          sum(1 for x in tr if x["t_race_entry_type"] == 1 and not x["t_race"]),
          sum(1 for x in tr if x["t_race_entry_type"] == 0 and x["t_race"])))
    p("  逐回合（末段）：表侧 entry × 每 run 是否比赛 × 801 卡槽数")
    p("     turn  entry  比赛run/总run  练了run/总run  槽数分布(801在板)  2槽")
    for t in sorted(fin):
        g = [x for x in rows if x["turn"] == t and x["src"] == "801" and x["on_board"]]
        rr = [x for x in tr if x["turn"] == t]
        p("     %-5d %-5s  %-12s %-13s %-18s %d" % (
            t, args.tbl.get(t, {}).get("race_entry_type"),
            "%d/%d" % (sum(1 for x in rr if x["t_race"]), len(rr)),
            "%d/%d" % (sum(1 for x in rr if x["t_train"]), len(rr)),
            dict(sorted(collections.Counter(x["n_slot"] for x in g).items())),
            sum(1 for x in g if x["n_slot"] >= 2)))
    p("  末段逐 run（格＝entry/是否比赛/该 run 这块板上 801 卡的槽数，多张卡用 + 连）：")
    p("     run     " + " ".join("%-13s" % ("t%d" % t) for t in sorted(fin)))
    for r in sorted({x["run"] for x in tr}):
        cells = []
        for t in sorted(fin):
            xs = [x for x in tr if x["run"] == r and x["turn"] == t]
            if not xs:
                cells.append("-")
                continue
            x = xs[0]
            slots = [str(y["n_slot"]) for y in rows
                     if y["run"] == r and y["turn"] == t and y["src"] == "801" and y["on_board"]]
            cells.append("%s/%s/%s" % (x["t_race_entry_type"], x["t_race"],
                                       "+".join(slots) if slots else "无卡"))
        p("     %-7s %s" % (r, " ".join("%-13s" % c for c in cells)))
    g = [x for x in rows if x["turn"] in fin and x["src"] == "801" and x["on_board"]]
    ct = collections.Counter((x["t_race"], x["n_slot"]) for x in g)
    et = collections.Counter((x["t_race_entry_type"], x["n_slot"]) for x in g)
    tt = collections.Counter((x["t_train"], x["n_slot"]) for x in g)
    p("  末段 801 卡回合：(当回合是否比赛, 槽数) %s" % dict(sorted(ct.items(), key=str)))
    p("  末段 801 卡回合：(表侧 race_entry_type, 槽数) %s" % dict(sorted(et.items(), key=str)))
    p("  末段 801 卡回合：(当回合是否练了, 槽数) %s" % dict(sorted(tt.items(), key=str)))
    div = sorted({(x["run"], x["turn"]) for x in g
                  if bool(x["t_race"]) != (x["t_race_entry_type"] == 1)})
    p("  ⇒ 「比赛」与「entry 奇偶」在这份数据里分开的 (run,turn)：%d 个 %s" % (len(div), div[:20]))
    p("     （非空才能判因果；为空 ⇒ 两者同向，登记为「不可分」）")

    p("\n## H. 其它两个来源（合宿牵绊融合 / 末段 period==3）的可见率同表")
    for src in ("bond", "fin"):
        g = [x for x in rows if x["src"] == src and x["on_board"]]
        if not g:
            p("  %s：没有样本" % src)
            continue
        for lab, sel in (("非比赛", lambda x: not x["t_race"]), ("比赛", lambda x: bool(x["t_race"])),
                         ("练了", lambda x: x["t_train"]), ("没练", lambda x: not x["t_train"])):
            h = [x for x in g if sel(x)]
            p("  %-5s %-4s 卡回合%5d 可见%5d 可见率%7.2f%%" % (
                src, lab, len(h), sum(x["vis"] for x in h),
                100.0 * sum(x["vis"] for x in h) / max(len(h), 1)))
        p("  %-5s 不在板上的卡回合 %d" % (
            src, sum(1 for x in rows if x["src"] == src and not x["on_board"])))
        bad = [x for x in g if not x["vis"]]
        p("  %-5s 不可见的 %d 行逐条（看是不是同一张卡）：" % (src, len(bad)))
        for x in sorted(bad, key=lambda y: (int(y["turn"]), y["run"])):
            p("       run%-5s t%-3d card%-7s 比赛%s 练了%s 彩圈%s 演目state%s advice%s 体力%s 动机%s heads%s"
              % (x["run"], x["turn"], x["card_id"], x["t_race"], x["t_train"], x["own_act_lit"],
                 x["own_act_state"], x["advice_obj"], x["vital"], x["motivation"], x["heads_sum"]))
        ok = [x for x in g if x["vis"] and x["card_id"] in {y["card_id"] for y in bad}]
        for cid in sorted({y["card_id"] for y in bad}):
            h = [x for x in g if x["card_id"] == cid]
            p("       ↳ card%-7s 该来源下共 %d 卡回合，可见 %d｜彩圈取值 可见%s／不可见%s"
              % (cid, len(h), sum(x["vis"] for x in h),
                 sorted({x["own_act_lit"] for x in h if x["vis"]}),
                 sorted({x["own_act_lit"] for x in h if not x["vis"]})))

    p("\n## I. #47 §7-3 点名的六个回合（t54/59/60/68/70/72）逐个交代")
    for t in (54, 59, 60, 68, 70, 72):
        g = [x for x in rows if x["turn"] == t and x["src"] == "801"]
        rr = [x for x in tr if x["turn"] == t]
        p("  t%-3d 801卡回合%3d 板上%3d 可见%3d｜该回合比赛的 run %s｜练了的 run %s" % (
            t, len(g), sum(x["on_board"] for x in g), sum(x["vis"] for x in g),
            sorted({x["run"] for x in rr if x["t_race"]}),
            sorted({x["run"] for x in rr if x["t_train"]})))
        per = collections.defaultdict(list)
        for x in g:
            per[x["run"]].append("%s:%d" % ("板" if x["on_board"] else "不在板", x["n_slot"]))
        for r in sorted(per):
            x = [y for y in rr if y["run"] == r]
            p("       run%-6s race%s train%s 槽 %s" % (
                r, x[0]["t_race"] if x else "?", x[0]["t_train"] if x else "?",
                " ".join(per[r])))

    p("\n## J. 「不可见」是不是取板口径 / 板本身退化造成的（口径 1 的反证检验）")
    g = [x for x in rows if x["src"] == "801" and x["on_board"]]
    inv = [x for x in g if not x["vis"]]
    rescued = [x for x in inv if x["n_slot_any_ps1"] > 0]
    p("  决策板 0 槽的 801 在板卡回合 %d｜其中「同一 (run,turn) 的*另一块* ps==1 板给了槽」%d"
      % (len(inv), len(rescued)))
    if rescued:
        p("  ⇒ 非 0 的行（说明需改用「任一板最大值」口径重算）：%s" %
          sorted({(x["run"], x["turn"]) for x in rescued})[:20])
    else:
        p("  ⇒ 0 行 ⇒ 不可见不是「取了哪块板」的口径问题：这个回合的每一块训练板都没有它")
    p("  改用「该回合任一 ps==1 板的最大槽数」后仍是 0 槽的：%d 行（与决策板口径的 %d 行比较，"
      "相等 ⇒ 两种口径给出同一批例外）" % (
          sum(1 for x in g if not x["n_slot_any_ps1"]), len(inv)))
    off = [x for x in rows if x["src"] == "801" and not x["on_board"]]
    p("  801 有来源但决策板上根本没有站位的 %d 行：全在比赛回合 %s｜其中当回合练了的 %d"
      % (len(off), "是" if all(x["t_race"] for x in off) else "否",
         sum(1 for x in off if x["t_train"])))
    p("     按 turn：%s" % dict(sorted(collections.Counter(x["turn"] for x in off).items())))
    p("  板退化程度（决策板 Σheads）× 比赛：")
    for lab, sel in (("比赛回合", lambda x: x["t_race"]), ("非比赛回合", lambda x: not x["t_race"])):
        h = [x["heads_sum"] for x in g if sel(x)]
        if not h:
            continue
        s = sorted(h)
        p("     %-8s 卡回合%4d  中位%4d  最小%3d  最大%3d  heads==0 的卡回合 %d" % (
            lab, len(h), s[len(s) // 2], s[0], s[-1], sum(1 for v in h if v == 0)))
    p("  heads 不是「看不见」的原因（同层双向都有例外 ⇒ 只是伴随比赛而变薄）：")
    hi = [x for x in g if x["t_race"] and x["heads_sum"] >= 17]
    lo = [x for x in g if not x["t_race"] and x["heads_sum"] < 17]
    p("     比赛且 heads>=17：%d 卡回合，可见 %d｜非比赛且 heads<17：%d 卡回合，可见 %d" % (
        len(hi), sum(x["vis"] for x in hi), len(lo), sum(x["vis"] for x in lo)))
    p("  任务书 §7-3 的缺口按「这一回合到底练没练」重定口径后的 0 例外检查：")
    for lab, sel in (("在板 且 非比赛", lambda x: not x["t_race"]),
                     ("在板 且 练了", lambda x: x["t_train"]),
                     ("在板 且 练了且没比赛", lambda x: x["t_train"] and not x["t_race"])):
        h = [x for x in g if sel(x)]
        ninv = sum(1 for x in h if not x["vis"])
        p("     %-16s 卡回合%4d 不可见%3d  2槽%3d  ⇒ 0 例外%s" % (
            lab, len(h), ninv, sum(1 for x in h if x["n_slot"] >= 2),
            "（成立）" if ninv == 0 else "（不成立）"))
    rr = [x for x in tr if x["t_race"] and x["t_train"]]
    p("  「同一回合既比赛又练了」（报文里像比赛后用续跑再练一手）的回合 %d 个：%s" % (
        len(rr), sorted({(x["run"], x["turn"]) for x in rr})))
    q = [x for x in g if x["t_race"] and x["t_train"]]
    p("     这些回合里落在 801 卡在板上的卡回合 %d，不可见 %d（n<3 ⇒ 只登记，不作结论）" % (
        len(q), sum(1 for x in q if not x["vis"])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=RAW)
    ap.add_argument("--mdb", default=MDB)
    ap.add_argument("--turn-csv", default=OUT_TURN)
    ap.add_argument("--card-csv", default=OUT_CARD)
    ap.add_argument("--txt", default=OUT_TXT)
    args = ap.parse_args()
    tbl = cpi.load_tables(args.mdb)
    args.tbl = turn_table(args.mdb)
    pk, acts, req, keys, names = walk(args, tbl[2])
    args.pk, args.acts, args.req = pk, acts, req
    # 比赛标记：该 (run,turn) 的任一包 chara_info.race_program_id != 0
    race_by = {}
    for (r, t), v in pk.items():
        race_by[(r, t)] = 1 if any(isinstance(x[5].get("race_program_id"), int)
                                   and x[5]["race_program_id"] for x in v) else 0
    args.race_by = race_by
    rows, turns = card_rows(pk, tbl, args)
    if not rows:
        print("!! 没抽到任何带来源的卡回合")
        return
    for x in rows:
        x["t_race"] = race_by.get((x["run"], x["turn"]), 0)
        x["dec_file"] = turns[(x["run"], x["turn"])][1]
    cols = list(rows[0].keys())
    with open(args.card_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    trows = []
    for (r, t), rec in sorted(turns.items(), key=lambda x: (x[0][0], x[0][1])):
        d = feat_of(rec, t, pk[(r, t)], acts.get(r, []), req.get(r, []), args)
        d["run"] = r
        d["t_race"] = race_by[(r, t)]
        d["dec_file"] = rec[1]
        g = [x for x in rows if x["run"] == r and x["turn"] == t]
        for src in ("801", "bond", "fin"):
            d["n_%s_card" % src] = sum(1 for x in g if x["src"] == src)
            d["n_%s_onboard" % src] = sum(1 for x in g if x["src"] == src and x["on_board"])
            d["n_%s_vis" % src] = sum(1 for x in g if x["src"] == src and x["vis"])
        trows.append(d)
    args.trows = trows
    with open(args.turn_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(trows[0].keys()))
        w.writeheader()
        w.writerows(trows)
    with open(args.txt, "w", encoding="utf-8") as fh:
        report(rows, turns, keys, names, tbl, args, fh)
    with open(args.txt, encoding="utf-8") as fh:
        sys.stdout.write(fh.read())
    print("\n产物：%s（%d 卡回合）｜%s（%d 回合）｜%s" % (
        args.card_csv, len(rows), args.turn_csv, len(trows), args.txt))


if __name__ == "__main__":
    main()
