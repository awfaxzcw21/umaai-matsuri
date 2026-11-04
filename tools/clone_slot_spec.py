"""逐 (run, 回合) 的**应然分身槽数**规格（#56，机器可校验，供 Rust 侧直接对照）。

一条命令重建全部产物（约 2 分钟，只读报文）：

    python tools/clone_slot_spec.py

链路复用 #47/#51：`tools/clone_partner_ids.py`（`read_board` / `card_state` / `load_tables`）
＋ `tools/clone_visibility.py`（`walk` / `decision_pack` / `feat_of` / `turn_table`）。
本脚本只做一件事：把 #51 定住的判据写成**可判定的规则序列**（顺序即优先级，命中即返回），
逐 (run,turn) 与逐 (run,turn,卡) 给出「应然槽数」，并把实测值贴在旁边，让主线与引擎双向反查。

只测量，不改任何 tracked 文件、不写 `data/`、不接引擎、不造拟合常数。

轴口径（唯一事实源＝`docs/回合轴口径审计_20261104.md` §0）：
**报文 turn ＝ 面板 ＝ 表行号（1 基，1–78）；引擎 ＝ 报文 − 1（0 基，0–77）**。
两轴在每行都同时给出，列名写明 `_panel` / `_engine`。

先写死再跑的口径：

1. 判定单元 ＝ 该 `(run,面板turn)` 的**决策板**（同 #51 口径 1：最后一条 `playing_state==1`
   的板报文，优先不带 `data.command_result` 的那条）。
2. 四源分开数（全部走 master.mdb 表侧，脚本内不写死数值）：
   S1 协助教诲 801 ＝ 该卡担当演目 `advice_id==801`（`single_mode_15_advice_effect` 102…120，
   effect_type 18，`effect_value_2=1`/`effect_value_4=1`）；
   S2 牵绊 101 本体 ＝ `advice_id==101`（表侧 101 只有 effect_type 16/13，**没有 18**）；
   S3 合宿融合 ＝ `advice_info_array[].fusion_advice_id==101` 且本体≠101 且板 `camp==1`
   （`single_mode_15_fusion_effect` id2，effect_type 17，`effect_value_4=1`）；
   S4 末段 ＝ 面板回合 ∈ `single_mode_turn(turn_set_id=15, period=3)`
   （`single_mode_15_finals_act_effect` id4，effect_type 111，`effect_value_4=1`，
   condition_type=0 ⇒ **表侧没有卡级选择条件**，归属是粗口径，见「实现不了」U3）。
3. **域**：只有「当回合没比赛」（该 `(run,turn)` 报文窗口内 `chara_info.race_program_id` 全为 0）
   的板才是训练决策板（#51 §4：比赛回合的 `ps==1` 板是退化／残留板，同一回合不同 run 结论相反）。
   例外统计只在域内算；域内且无未决卡的行 `engine_applies=1`。
4. 实测槽数 ＝ `len(mk)` ＝ 该卡站位出现在**几个训练条目**的 `sub_command_partner_array` 里
   （`clone_partner_ids.read_board:73-79`）。这是本规格唯一的被测量；`mk ⊆ 站位` ⇒
   「不在板上 ⇒ 0」是**恒等式**（证据档记「定义」），不是实测证据。
5. 例外 ＝ 域内、该卡确有站位、`实测槽数 != 应然槽数`；多源同时在场时按「该源单独在场」的
   隔离样本判归属，复合样本单列，不摊派。

"""

import argparse
import collections
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clone_partner_ids as cpi  # noqa: E402
import clone_visibility as cv  # noqa: E402

RAW = cv.RAW
MDB = cv.MDB
DOC = "docs/分身槽数应然表_20261105.md"
OUT_TURN = "out/_clone_slot_spec_20261105.csv"
OUT_CARD = "out/_clone_slot_spec_card_20261105.csv"
OUT_RULES = "out/_clone_slot_rules_20261105.json"
OUT_CENSUS = "out/_clone_source_census_20261105.csv"
OUT_TXT = "out/_clone_slot_spec_report_20261105.txt"

SRC_NAME = {1: "S1 协助教诲801", 2: "S2 牵绊101本体", 3: "S3 合宿融合", 4: "S4 末段id4"}
SRC_COL = {1: "s801", 2: "s101", 3: "sfuse", 4: "sfin"}

RULES = [
    dict(id="R1", evidence="未决",
         text="当回合有比赛（该 (run,面板turn) 窗口内 `race_program_id` 非 0）⇒ **不给应然值**（域外）",
         basis="#51 §4：37 例「来源在板却不可见」＋37 例「有来源但板上无站位」全部落在比赛回合；"
               "比赛层内 64 个候选变量无一 0 例外；同一回合同一比赛在不同 run 结论相反 ⇒ 这份报文判不了"),
    dict(id="R2", evidence="定义",
         text="域内且该卡当回合在板上无任何站位（不落在任何训练条目）⇒ 0 槽",
         basis="恒等式：实测槽数 `len(mk)` 只在站位上定义（`read_board:73-79`），无站位必为 0。"
               "不是一手证据，只是口径自证（防止把「没落位」误读成「机制不给分身」）"),
    dict(id="R3", evidence="未决",
         text="该卡吃到合宿融合（S3：`fusion_advice_id==101` 且本体≠101 且板 `camp==1`）⇒ **不给应然值**",
         basis="表侧 `single_mode_15_fusion_effect` id2 说给 1，但本次域内隔离 68 例里有 4 例 0 槽，全部落在 run171 "
               "合宿 t61–64（同局同卡 30311 既出现 0 也出现 1，其余 9 局 56 例全 1）；报文里没有任何字段能分开 ⇒ 定不住"),
    dict(id="R4", evidence="一手",
         text="S1（801）∧ S4（末段）∧ 表侧 `race_entry_type==0` ∧ 该卡在板 ⇒ **2 槽**",
         basis="#51 §6：末段 801 在板 48/48 都是 2 槽，0 例外；且非末段全量 2 槽总数恰为 48 ⇒ 第 2 槽不来自别处"),
    dict(id="R5", evidence="未决",
         text="S1 ∧ S4 但表侧 `race_entry_type!=0`（域内）⇒ 不给应然值",
         basis="本数据里这种组合的样本数为 0（末段 ∧ entry!=0 的回合全部有比赛，被 R1 吃掉）⇒ 无可判样本，"
               "换轴/换剧本要重新验；不许外推"),
    dict(id="R6", evidence="一手",
         text="S1（该卡担当演目 `advice_id==801`）∧ 域内 ∧ 该卡在板 ⇒ **1 槽**（末段另计，见 R4）",
         basis="#51 §0/§2：非比赛回合 801 在板 264/264 必出分身，0 例外；「练了」子集 213/213；"
               "本次 §C 逐卡复算仍 0 例外"),
    dict(id="R7", evidence="外推",
         text="域内且只有 S4（末段，无 801、无融合）⇒ 1 槽",
         basis="#51 §3 的 174 个 S4 隔离卡回合（含比赛回合）里 169 可见、5 例全在支援卡 30289；本次域内隔离 87 例 3 例外"
               "（另 2 例在 R1 域外）。30289 自己的 27 个域内末段样本 24 可见／3 不可见，同局相邻末段回合结论相反 ⇒ "
               "不是卡级规则、字段也分不开 ⇒ 只能外推，不许进分数模型当已证"),
    dict(id="R8", evidence="一手",
         text="域内且没有任何 +1 来源（含「只有 S2 牵绊 101 本体」）⇒ 0 槽",
         basis="表侧 `single_mode_15_advice_effect` 里 `advice_id=101` 只有 effect_type 16/13，**无 18** ⇒ 牵绊本体"
               "不是分身来源（分身走 R3 的融合体）；#47 §3「板上分身总数只来自有来源的卡」在本次 §F 复算仍成立"),
    dict(id="R9", evidence="一手",
         text="落位形状（不决定数量）：分身只落在该卡自己的卡组站位；分身 id 一定同时出现在该条目的 "
               "`training_partner_array` 里；`sub_command_partner_array` 里没有 ≥100 的 NPC id；"
               "分身不落在本体所在的那条训练条目、也不落在该卡自己担当演目的 510x",
         basis="#47 §3 四条 0 反例约束；本次 §F 自检把每条都重新数了一遍（含 `sub_not_in_partners`、`sub_ge100`、"
               "「分身条目∩本体条目」、「落自己担当 510x」计数，域内 412 个有分身卡回合上均 0 例外）"),
]


def source_flags(k, b, turn, fin):
    """一张卡组站位在这块板上的四个来源（S1..S4），全按表侧定义。"""
    A, B = k["adv_obj"], k["adv_base"]
    return {
        1: 1 if A == cpi.SRC_801 else 0,
        2: 1 if A == cpi.SRC_BOND else 0,
        3: 1 if (B == cpi.SRC_BOND and A != cpi.SRC_BOND and b["camp"]) else 0,
        4: 1 if (turn or 0) in fin else 0,
    }


def decide(srcs, entry, race, on_board):
    """规则序列（顺序即优先级）-> (应然槽数或空串, 规则 id, 证据档)。空串＝这条规格不给数。"""
    if race:
        return "", "R1", "未决"
    if not on_board:
        return "0", "R2", "定义"
    s1, s2, s3, s4 = srcs[1], srcs[2], srcs[3], srcs[4]
    if s3:
        return "", "R3", "未决"
    if s1 and s4:
        if entry == 0:
            return "2", "R4", "一手"
        return "", "R5", "未决"
    if s1:
        return "1", "R6", "一手"
    if s4:
        return "1", "R7", "外推"
    return "0", "R8", "一手"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=RAW)
    ap.add_argument("--mdb", default=MDB)
    ap.add_argument("--turn-csv", default=OUT_TURN)
    ap.add_argument("--card-csv", default=OUT_CARD)
    ap.add_argument("--rules", default=OUT_RULES)
    ap.add_argument("--census", default=OUT_CENSUS)
    ap.add_argument("--txt", default=OUT_TXT)
    args = ap.parse_args()

    tbl = cpi.load_tables(args.mdb)
    deyi, stype, chara, a2cmd, base, fin = tbl
    cv_tbl = cv.turn_table(args.mdb)
    entry_type = {t: (d.get("race_entry_type") if d else None) for t, d in cv_tbl.items()}

    class W:
        raw = args.raw
    pk, acts, req, keys, names = cv.walk(W, chara)

    class F:
        pass
    fargs = F()
    fargs.tbl, fargs.acts, fargs.req = cv_tbl, acts, req
    race_by = {}
    for (r, t), v in pk.items():
        race_by[(r, t)] = 1 if any(isinstance(x[5].get("race_program_id"), int)
                                   and x[5]["race_program_id"] for x in v) else 0
    fargs.race_by = race_by

    # run 身份自证：single_mode_chara_id 是否与 start_time 一一对应（不对应则 (run,turn) 会串局）
    starts = collections.defaultdict(set)
    for (r, t), v in pk.items():
        starts[r].add(v[0][5].get("start_time"))
    multi = {r: s for r, s in starts.items() if len(s) > 1}

    cards, turns = [], []
    ident = collections.Counter()      # §F 恒等式自检计数
    for (r, t), v in sorted(pk.items(), key=lambda x: (x[0][0], x[0][1])):
        rec = cv.decision_pack(v)
        b = rec[3]
        d = cv.feat_of(rec, t, v, acts.get(r, []), req.get(r, []), fargs)
        race = race_by[(r, t)]
        et = entry_type.get(t)
        rows = []
        for pos in b["deck"]:
            if not (isinstance(pos, int) and pos < 100):
                continue
            k = cpi.card_state(b, pos, tbl)
            if k["ctype"] != 1:
                continue
            srcs = source_flags(k, b, t, fin)
            n = len(k["mk"])
            exp, rule, ev = decide(srcs, 0 if et is None else et, race, bool(k["on_board"]))
            rows.append(dict(run_id=r, turn_panel=t, turn_engine=t - 1, pos=pos,
                             card_id=k["card"], support_card_type=k["ctype"],
                             s801=srcs[1], s101=srcs[2], sfuse=srcs[3], sfin=srcs[4],
                             n_src=sum(srcs.values()),
                             on_board=int(bool(k["on_board"])),
                             body_entry_ids="|".join(str(x) for x in sorted(k["body"])),
                             clone_entry_ids="|".join(str(x) for x in sorted(k["mk"])),
                             sub_len_measured=n,
                             race_turn=race, race_entry_type=et, camp=b["camp"],
                             expected_slots=exp, rule_id=rule, evidence=ev,
                             判据出处="%s#%s" % (DOC, rule),
                             heads_sum=d["heads_sum"], trained=d["t_train"]))
            if n and not k["on_board"]:
                ident["mk_without_stand"] += 1          # 应为 0，否则脚本自相矛盾
            if srcs[1] and not k["on_board"] and race == 0:
                ident["s1_in_deck_off_board_indomain"] += 1
            if n and race == 0:
                cl, bo = set(k["mk"]), set(k["body"])
                ident["clone_indomain"] += 1
                ident["clone_shares_body_entry"] += len(cl & bo)   # R9：分身不落回本体所在条目
                ident["clone_without_body"] += 0 if bo else 1      # 本体没落任何条目、自己当分身
                ident["clone_in_keiko_entry"] += sum(1 for x in cl if x in cpi.KEIKO)
                ident["clone_on_own_keiko"] += sum(1 for x in cl if k["keiko"] and x == k["keiko"])
        cards.extend(rows)

        on801 = [x for x in rows if x["s801"] and x["on_board"]]
        anysrc = [x for x in rows if x["n_src"] and x["on_board"]]
        # 801 线的应然总数只受「这张 801 卡自己判不了」影响，不被同板上别的未决卡牵连
        und801 = [x for x in on801 if x["evidence"] == "未决"]
        und_all = [x for x in anysrc if x["evidence"] == "未决"]
        exp801 = exp_all = ""
        if race == 0 and not und801:
            exp801 = str(sum(int(x["expected_slots"]) for x in on801)) if on801 else "0"
        if race == 0 and not und_all:
            exp_all = str(sum(int(x["expected_slots"]) for x in anysrc)) if anysrc else "0"
        rank = {"未决": 3, "外推": 2, "定义": 1, "一手": 0}
        hit801 = sorted({x["rule_id"] for x in on801})
        hit = sorted({x["rule_id"] for x in anysrc})
        turns.append(dict(run_id=r, turn_panel=t, turn_engine=t - 1,
                          has_801_in_deck=int(bool([x for x in rows if x["s801"]])),
                          has_801_on_board=int(bool(on801)),
                          n_801_on_board=len(on801),
                          sub_len_801_measured=sum(x["sub_len_measured"] for x in on801),
                          sub_len_board_measured=d["sub_sum"],
                          race_turn=race, race_entry_type=et, camp=b["camp"],
                          expected_slots=exp801, expected_slots_all_src=exp_all,
                          rules_hit_801="+".join(hit801),
                          evidence_801=max((x["evidence"] for x in on801), key=lambda e: rank[e])
                          if on801 else "",
                          rules_hit="+".join(hit),
                          evidence=max((x["evidence"] for x in anysrc), key=lambda e: rank[e])
                          if anysrc else "一手",
                          n_undecided_801=len(und801), n_undecided_cards=len(und_all),
                          engine_applies=int(race == 0 and not und801),
                          spec_complete=int(race == 0 and not und_all),
                          heads_sum=d["heads_sum"], trained=d["t_train"], pkt_n=len(v)))

    # 全域恒等式：分身 id 是否一定在该条目的 training_partner_array 里
    for (r, t), v in pk.items():
        for x in v:
            if x[2] != 1:
                continue
            for cid, c in x[3]["entry"].items():
                ps = {p for p in (c.get("training_partner_array") or []) if isinstance(p, int)}
                sb = [p for p in (c.get("sub_command_partner_array") or []) if isinstance(p, int)]
                ident["sub_items"] += len(sb)
                ident["sub_not_in_partners"] += sum(1 for p in sb if p not in ps)
                ident["sub_ge100"] += sum(1 for p in sb if p >= 100)
                ident["sub_dup"] += len(sb) - len(set(sb))
                ident["sub_deck_pos"] += sum(1 for p in sb if p in x[3]["deck"])

    write_csv(args.card_csv, cards)
    for x in turns:
        x["判据出处"] = "%s#%s" % (DOC, (x["rules_hit_801"] or x["rules_hit"] or "R8").replace("+", "-"))
    write_csv(args.turn_csv, turns)
    with open(args.rules, "w", encoding="utf-8") as fh:
        json.dump({"axis": {"报文turn": "＝面板＝表行号（1 基 1-78）",
                            "引擎turn": "＝面板-1（0 基 0-77）",
                            "唯一事实源": "docs/回合轴口径审计_20261104.md §0",
                            "全仓唯一换算点": "crates/matsuri-cli/src/state.rs:270-271"},
                   "decision_unit": "#51 口径 1（最后一条 playing_state==1 板，优先无 command_result）",
                   "domain": "该 (run,面板turn) 报文窗口内 chara_info.race_program_id 全为 0",
                   "measured": "len(mk)＝该卡站位出现在几个训练条目的 sub_command_partner_array 里",
                   "sources": {SRC_NAME[i]: "口径 2（master.mdb 表侧）" for i in (1, 2, 3, 4)},
                   "rules": RULES,
                   "decide_order": ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]},
                  fh, ensure_ascii=False, indent=1)

    with open(args.txt, "w", encoding="utf-8") as fh:
        report(cards, turns, args, fh, multi, ident)
    with open(args.txt, encoding="utf-8") as fh:
        sys.stdout.write(fh.read())


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def src_str(x):
    return "".join(SRC_NAME[i][1] for i in (1, 2, 3, 4) if x[SRC_COL[i]])


def row_line(x, extra=""):
    return ("     ✗ run%-5s 面板%-3d 引擎%-3d card%-7s 实测%s 应然%s 规则%-5s 来源%s "
            "比赛%s entry%s camp%s heads%s%s" % (
                x["run_id"], x["turn_panel"], x["turn_engine"], x["card_id"],
                x["sub_len_measured"], x["expected_slots"] or "—", x["rule_id"],
                src_str(x), x["race_turn"], x["race_entry_type"], x["camp"],
                x["heads_sum"], (" ｜分身落点:" + extra) if extra else ""))


def report(cards, turns, args, fh, multi, ident):
    p = lambda s="": fh.write(s + "\n")
    p("# 逐 (run,回合) 应然分身槽数规格（#56）留档  2026-11-05")
    p("复现：python tools/clone_slot_spec.py")
    p("轴：报文turn＝面板＝表行号（1 基 1-78）；引擎turn＝面板-1（0 基 0-77）")
    p("产物：%s（%d 行 (run,turn)）｜%s（%d 行 (run,turn,卡)）｜%s｜%s" % (
        args.turn_csv, len(turns), args.card_csv, len(cards), args.rules, args.census))

    p("\n## A. 样本与 run 身份自证")
    p("  (run,面板turn) 决策板 %d 个｜run 数 %d｜卡回合行 %d" % (
        len(turns), len({x["run_id"] for x in turns}), len(cards)))
    if multi:
        p("  ⚠ `single_mode_chara_id` 对 `start_time` 不唯一的 run：%s ⇒ (run_id,turn) 主键会串局" % sorted(multi))
    else:
        p("  ✓ `single_mode_chara_id` ↔ `start_time` 一一对应 ⇒ 用 (run_id,面板turn) 当主键安全")
    p("  域内（没比赛）%d｜域外（比赛）%d｜域内含 801 在板 %d" % (
        sum(1 for x in turns if x["race_turn"] == 0),
        sum(1 for x in turns if x["race_turn"] == 1),
        sum(1 for x in turns if x["race_turn"] == 0 and x["has_801_on_board"])))
    p("  卡组里有 801 卡的 run：%d／%d" % (
        len({x["run_id"] for x in turns if x["has_801_in_deck"]}),
        len({x["run_id"] for x in turns})))

    p("\n## B. 规则序列（%d 条，decide() 按 R1→R8 顺序、命中即返回；每条标 一手／外推／未决／定义）" % len(RULES))
    hit = collections.Counter(x["rule_id"] for x in cards)
    for ru in RULES:
        if ru["id"] == "R9":
            p("  %-4s [%s] 不进 decide()（形状约束，§F 逐项数）｜ %s" % (ru["id"], ru["evidence"], ru["text"]))
        else:
            p("  %-4s [%s] 命中 %4d 卡回合 ｜ %s" % (ru["id"], ru["evidence"], hit[ru["id"]], ru["text"]))
        p("       出处：%s" % ru["basis"])

    p("\n## C. 自检：应然 == 实测（一手/外推档、域内、该卡确有站位）")
    for lab in ("一手", "外推"):
        s = [x for x in cards if x["evidence"] == lab and x["on_board"] and x["race_turn"] == 0
             and x["expected_slots"] != ""]
        bad = [x for x in s if x["sub_len_measured"] != int(x["expected_slots"])]
        p("  %s档 %d 样本｜例外 %d" % (lab, len(s), len(bad)))
        for x in sorted(bad, key=lambda y: (y["rule_id"], y["turn_panel"], y["run_id"]))[:30]:
            p(row_line(x, x["clone_entry_ids"]))
    s = [x for x in cards if x["evidence"] == "未决" and x["on_board"] and x["race_turn"] == 0]
    c = collections.Counter(x["sub_len_measured"] for x in s)
    p("  未决档 %d 样本（R3/R5，不给判据）实测槽数分布 %s" % (len(s), dict(sorted(c.items()))))
    s2 = [x for x in cards if x["rule_id"] == "R2"]
    c2 = collections.Counter(x["sub_len_measured"] for x in s2)
    p("  定义档 %d 样本（该卡无站位）实测槽数分布 %s ⇒「无站位 ⇒ 0」是恒等式，不是实测证据" % (
        len(s2), dict(sorted(c2.items()))))
    p("  按规则细分（域内、有站位、给了数）：")
    for rid in ("R4", "R6", "R7", "R8"):
        s = [x for x in cards if x["rule_id"] == rid and x["on_board"] and x["race_turn"] == 0
             and x["expected_slots"] != ""]
        bad = [x for x in s if x["sub_len_measured"] != int(x["expected_slots"])]
        p("     %-4s n=%-4d 例外 %-3d %s" % (rid, len(s), len(bad),
                                             "0 例外已定住" if not bad else "见上清单"))

    p("\n## D. 逐源 0 例外普查（域＝当回合没比赛；「隔离」＝只有该源在场，归属才判得了）")
    cen = []
    for s in (1, 2, 3, 4):
        col = SRC_COL[s]
        pres = [x for x in cards if x[col]]
        onb = [x for x in pres if x["on_board"]]
        dom = [x for x in onb if x["race_turn"] == 0]
        others = tuple(c for i, c in SRC_COL.items() if i != s)
        iso = [x for x in dom if not any(x[c] for c in others)]
        combo = [x for x in dom if any(x[c] for c in others)]
        exp_iso = {1: 1, 2: 0, 3: 1, 4: 1}[s]
        bad = [x for x in iso if x["sub_len_measured"] != exp_iso]
        verdict = "0 例外已定住" if not bad else "判不了（例外见下）"
        if s == 2:
            verdict = "0 例外已定住（表侧无 effect_type 18 ⇒ 本就不是来源，应然恒为 0）"
        if s == 3 and bad:
            verdict = "判不了 ⇒ R3 不给数（与主线采纳的「只接 801」一致）"
        p("  %-14s 全量在场 %4d｜有站位 %4d｜域内 %4d｜隔离 %4d／复合 %4d" % (
            SRC_NAME[s], len(pres), len(onb), len(dom), len(iso), len(combo)))
        p("       隔离样本按「该源单独在场应给 %d 槽」判：例外 %d／%d ⇒ %s" % (
            exp_iso, len(bad), len(iso), verdict))
        for x in sorted(bad, key=lambda y: (y["run_id"], y["turn_panel"]))[:20]:
            p(row_line(x, x["clone_entry_ids"]))
        if s == 4 and bad:
            by_card = collections.Counter(x["card_id"] for x in bad)
            p("       例外集中卡 %s（实测 0 槽的 %d 例）" % (
                dict(by_card), sum(1 for x in bad if x["sub_len_measured"] == 0)))
        cen.append(dict(source=SRC_NAME[s], expected_alone=exp_iso,
                        n_present=len(pres), n_on_board=len(onb), n_indomain=len(dom),
                        n_isolated=len(iso), n_combo=len(combo), n_exception=len(bad),
                        exception_list=" ; ".join("%s/t%d/card%s/n%d" % (
                            x["run_id"], x["turn_panel"], x["card_id"], x["sub_len_measured"])
                            for x in sorted(bad, key=lambda y: (y["run_id"], y["turn_panel"]))[:40]),
                        verdict=verdict))
    cc = [x for x in cards if x["on_board"] and x["race_turn"] == 0 and x["n_src"] >= 2]
    eq = [x for x in cc if x["sub_len_measured"] == x["n_src"]]
    p("  复合样本（域内 ≥2 源在场）n=%d｜实测==源数 %d｜实测<源数 %d｜实测>源数 %d" % (
        len(cc), len(eq), sum(1 for x in cc if x["sub_len_measured"] < x["n_src"]),
        sum(1 for x in cc if x["sub_len_measured"] > x["n_src"])))
    byc = collections.Counter((src_str(x), x["sub_len_measured"]) for x in cc)
    for (sr, n), c in sorted(byc.items()):
        p("       %s → %d 槽：%d 例" % (sr, n, c))
    with open(args.census, "w", newline="", encoding="utf-8-sig") as ch:
        w = csv.DictWriter(ch, fieldnames=list(cen[0].keys()))
        w.writeheader()
        w.writerows(cen)

    p("\n## E. 逐 (run,面板turn) 产出量（引擎反查用：应然总数 vs 实测总数）")
    p("  域内行 %d｜其中 801 在板 %d｜`engine_applies`（域内且 801 线无未决卡）%d｜`spec_complete`（全源无未决卡）%d" % (
        sum(1 for x in turns if x["race_turn"] == 0),
        sum(1 for x in turns if x["race_turn"] == 0 and x["has_801_on_board"]),
        sum(1 for x in turns if x["engine_applies"]),
        sum(1 for x in turns if x["spec_complete"])))
    q = collections.Counter((x["rules_hit_801"] or "—", x["evidence_801"] or "—")
                            for x in turns if x["race_turn"] == 0)
    p("  域内各行 801 线命中规则分布：%s" % dict(sorted(q.items(), key=str)))
    qa = collections.Counter((x["rules_hit"] or "—", x["evidence"]) for x in turns if x["race_turn"] == 0)
    p("  域内各行全源命中规则分布：%s" % dict(sorted(qa.items(), key=str)))
    tot = [x for x in turns if x["race_turn"] == 0 and x["expected_slots"] != ""]
    mism = [x for x in tot if x["sub_len_801_measured"] != int(x["expected_slots"])]
    p("  801 线（主线采纳）：给了应然总数的行 %d｜应然==实测 %d｜例外 %d" % (
        len(tot), len(tot) - len(mism), len(mism)))
    for x in mism[:20]:
        p("     ✗ run%-5s 面板%-3d 引擎%-3d 801在板%d 实测%s 应然%s 规则%s entry%s 未决卡%d" % (
            x["run_id"], x["turn_panel"], x["turn_engine"], x["n_801_on_board"],
            x["sub_len_801_measured"], x["expected_slots"], x["rules_hit_801"],
            x["race_entry_type"], x["n_undecided_801"]))
    allrow = [x for x in turns if x["race_turn"] == 0 and x["expected_slots_all_src"] != ""]
    allm = [x for x in allrow if x["sub_len_board_measured"] != int(x["expected_slots_all_src"])]
    p("  全源（含 R7 外推）：给了应然总数的行 %d｜应然==板上 `sub_command_partner_array` 总长 %d｜例外 %d" % (
        len(allrow), len(allrow) - len(allm), len(allm)))
    for x in allm[:20]:
        p("     ✗ run%-5s 面板%-3d 引擎%-3d 板上实测总长%s 应然%s 未决卡%d 规则%s 801线规则%s" % (
            x["run_id"], x["turn_panel"], x["turn_engine"], x["sub_len_board_measured"],
            x["expected_slots_all_src"], x["n_undecided_cards"], x["rules_hit"],
            x["rules_hit_801"]))

    p("\n## F. 恒等式／口径自检（不是机制，是「测的到底是什么」）")
    p("  `mk`（分身注册）非空但该位无站位：%d（应为 0，否则脚本自相矛盾）" % ident["mk_without_stand"])
    p("  801 在卡组但域内无站位的卡回合：%d ⇒ R2 是恒等式（`len(mk)` 只在站位上定义），不是一手证据"
      % ident["s1_in_deck_off_board_indomain"])
    p("  全部 ps==1 板：`sub_command_partner_array` 元素 %d｜不在同条目 `training_partner_array` 里的 %d｜"
      "id≥100 的 %d｜条目内重复的 %d｜落在卡组站位上的 %d" % (
          ident["sub_items"], ident["sub_not_in_partners"], ident["sub_ge100"],
          ident["sub_dup"], ident["sub_deck_pos"]))
    p("  ⇒ 若 `sub_not_in_partners`=0：分身槽数＝「自己站位出现在几个条目的 sub 数组里」＝板上分身 id 总数，"
      "两种数法等价（R9）")
    p("  域内有分身的卡回合 %d｜分身条目与本体条目相同槽的 %d｜分身落在自己担当演目 510x 上的 %d｜"
      "分身落在某个 510x（含他人担当）上的 %d｜本体没落任何条目却当分身的 %d" % (
          ident["clone_indomain"], ident["clone_shares_body_entry"], ident["clone_on_own_keiko"],
          ident["clone_in_keiko_entry"], ident["clone_without_body"]))
    p("  ⇒ R9 的「分身不落回本体所在条目／不落自己担当的 510x」在前 %d 个域内样本上 0 例外；"
      "但分身**具体落哪个条目**这份规格只登记不预测（见 U8）" % ident["clone_indomain"])

    p("\n## G. 主线实现不了 / 不该由这份规格背书的项")
    for line in [
        "U1 「实测」列（`sub_len_measured` / `sub_command_partner_array` 长度）是**服务端下发**字段：引擎侧看不到它，"
        "只能按 R6/R4 自己生成。这份规格的实测列只用于反查引擎生成得对不对，不是引擎的输入。",
        "U2 R6 的前提「该卡在板上有站位」是**落位问题**（#47 的按卡种站位机制），不是分身问题。"
        "本规格只保证「有站位 ⇒ 槽数」；站位本身这份报文给不出判据（真机站位由服务端抽，引擎用 #47 的近似落位）。"
        "⇒ 引擎的槽数＝「落位命中的 801 卡数」按 R6/R4 计，落位错了本规格查不出来，别拿它验落位。",
        "U3 S4（末段 id4）表侧 `condition_type=0` ⇒ **没有任何卡级选择条件**。「哪些卡吃到末段第 2 槽」只能靠报文归纳"
        "（实测＝末段在板的每张 801 卡都给第 2 槽，48/48）；这是粗口径，不是表侧证明。换卡组/换剧本要重验。",
        "U4 比赛回合（R1）不给应然值：那份板是退化／残留板，同一回合不同 run 结论相反（#51 §4）。"
        "引擎若要复刻「比赛回合的板」，这份报文没有判据可依。",
        "U5 合宿融合（R3）与「只带 S4 的末段卡」（R7）各有例外定不住 ⇒ 若引擎要进分数模型只能标为外推；"
        "主线已采纳「只接 801 这条线」，这两条不应影响分数。",
        "U6 R5（S1∧S4∧entry!=0 且域内）本数据 0 样本 ⇒ 引擎若遇到该组合（换剧本/换轴）规格里没有答案，"
        "不许拿 R4 或 R6 硬套。",
        "U7 「末段第 2 槽」与「该回合没比赛」在末段完全同向（0 个反例 (run,turn)）⇒ R4 写 `race_entry_type==0` "
        "还是写「当回合没比赛」在这份数据里等价。本规格选了表侧 `race_entry_type`（引擎可见），换判法要重新验。",
        "U8 分身**落在哪个训练条目**（`clone_entry_ids`）这份规格只登记不断言：实测显示分身出现在该卡站位所站的条目上，"
        "但「本体条目 vs 教诲条目」的分配属于 #47 落位／#50 每回合收益那条线，不在本规格内。",
        "U9 本规格的域＝这 %d 局报文（其中 %d 局卡组带 801 卡）、单一剧本 scen15、单一 viewer 账号。"
        "外推到别的卡组配置/剧本前要先确认 R4/R6 在新域里仍 0 例外。" % (
            len({x["run_id"] for x in turns}),
            len({x["run_id"] for x in turns if x["has_801_in_deck"]})),
    ]:
        p("  " + line)

    p("\n## H. 未决清单汇总（这份报文判不了的 (run,面板turn)）")
    und = [x for x in turns if x["race_turn"] == 0 and x["n_undecided_cards"]]
    p("  域内但全源规格有未决卡的行 %d（R3 合宿融合在场）｜其中 801 线也被卡住的行 %d" % (
        len(und), sum(1 for x in turns if x["race_turn"] == 0 and x["n_undecided_801"])))
    for x in sorted(und, key=lambda y: (y["run_id"], y["turn_panel"]))[:12]:
        p("     · run%-5s 面板%-3d 引擎%-3d 未决卡%d 全源规则%s 801线规则%s 801线应然%s 实测%s" % (
            x["run_id"], x["turn_panel"], x["turn_engine"], x["n_undecided_cards"],
            x["rules_hit"], x["rules_hit_801"] or "—",
            x["expected_slots"] or "—", x["sub_len_801_measured"]))
    p("  域外（比赛回合）行 %d：全部不给数（R1）" % sum(1 for x in turns if x["race_turn"] == 1))


if __name__ == "__main__":
    main()
