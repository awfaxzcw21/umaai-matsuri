"""主线独立复算 #66（`docs/分身槽数应然表_20261105.md`）的**承重数字**。

一条命令：

    python tools/clone_spec_recheck_20261105.py

三段，互不依赖，且**不复用** `clone_slot_spec.py` 的规则机与读板实现：

- A 段：只读两份 CSV，用**另一套聚合**把文档里的每个计数重新数一遍（含 §F 的 213/2138/477/413）。
- B 段：只读 `master.mdb`，验四源的表侧出处（801 独占 effect_type 18、101 无 18、融合 id2、末段 id4、
  `period=3` 的回合集合与 CSV 的 `sfin` 列逐位对齐）。
- C 段：**自己走一遍报文**（不用 `clone_visibility.walk`），按口径 1 取决策板，直接从
  `sub_command_partner_array` 数每卡槽数，与 CSV 的实测列逐格对；顺带重数 §F 的恒等式三条。

只读，不改任何 tracked 文件、不写 `data/`、不接引擎。
"""

import collections
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
TURN_CSV = "out/_clone_slot_spec_20261105.csv"
CARD_CSV = "out/_clone_slot_spec_card_20261105.csv"

PLAIN = {101, 102, 103, 105, 106}
KEIKO = {5101, 5102, 5103, 5104, 5105}

fails = []


def chk(name, got, want, note=""):
    ok = (got == want)
    print("%-4s %-58s got=%-10s want=%-10s %s" %
          ("OK" if ok else "FAIL", name, got, want, note))
    if not ok:
        fails.append(name)
    return ok


def show(name, val, note=""):
    print("     %-58s %-10s %s" % (name, val, note))


def board_ents(data):
    """一块板上的训练条目（只认板上真出现的 10x / 510x）。"""
    hi = data.get("home_info") or {}
    return {e.get("command_id"): e for e in hi.get("command_info_array") or []
            if e.get("command_id") in PLAIN or e.get("command_id") in KEIKO}


def read_csv(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def I(x):
    return int(x) if x not in ("", None) else None


# ---------------------------------------------------------------- A 段
def part_a(turns, cards):
    print("\n===== A. CSV 聚合复算（不碰规则机） =====")
    by_key = collections.defaultdict(list)
    for c in cards:
        by_key[(c["run_id"], c["turn_panel"])].append(c)
    for t in turns:
        chk_row = I(t["turn_panel"]) - 1 == I(t["turn_engine"])
        if not chk_row:
            fails.append("A1 轴换算")
            break
    show("A1 逐行 turn_engine == turn_panel-1", "全部成立" if not fails else "有违例")
    show("A1 面板回合范围", "%s..%s" % (min(I(t["turn_panel"]) for t in turns),
                                        max(I(t["turn_panel"]) for t in turns)))
    chk("A0 (run,turn) 行数", len(turns), 759)
    chk("A0 卡回合行数", len(cards), 3795)
    chk("A0 键集合一致", len({(c["run_id"], c["turn_panel"]) for c in cards}), len(turns))

    # 逐卡 rule 命中数（文档 §2 表）
    rc = collections.Counter(c["rule_id"] for c in cards)
    for rid, want in [("R1", 1410), ("R2", 92), ("R3", 68), ("R4", 48), ("R5", 0),
                      ("R6", 216), ("R7", 87), ("R8", 1874)]:
        chk("A2 %s 命中（卡回合级）" % rid, rc.get(rid, 0), want)
    chk("A2 命中数求和", sum(rc.values()), 3795)

    # 例外 = 域内 + 有站位 + 给了数 + 实测 != 应然
    # 注意口径：卡表 `expected_slots` 是**全源**应然（含 R7 外推），板表 `expected_slots`
    # 只是 801 线 ⇒ 一手/定义档必须 0 例外；外推档的例外是文档自己登记的 3 例，单列。
    ex_all = [c for c in cards if I(c["race_turn"]) == 0 and I(c["on_board"]) == 1
              and c["expected_slots"] != "" and I(c["expected_slots"]) != I(c["sub_len_measured"])]
    show("A3 卡表应然行的例外（含外推档）", "%d 例：%s" %
         (len(ex_all), sorted("%s/t%s/%s" % (c["run_id"], c["turn_panel"], c["card_id"]) for c in ex_all)))
    chk("A3 一手+定义档例外 = 0",
        len([c for c in ex_all if c["evidence"] in ("一手", "定义")]), 0)
    chk("A3 例外全部落在外推档且卡=30289",
        sorted({c["card_id"] for c in ex_all}), ["30289"])
    ev = collections.Counter(c["evidence"] for c in cards if c["expected_slots"] != "")
    show("A4 逐卡应然行的证据档", dict(ev))
    one = [c for c in cards if c["evidence"] == "一手" and c["expected_slots"] != ""]
    chk("A5 一手档逐卡 实测==应然", sum(1 for c in one if I(c["sub_len_measured"]) == I(c["expected_slots"])), len(one))
    chk("A5 一手档行数（R4+R6+R8）", len(one), 216 + 48 + 1874)

    # 域 / 应然总数（(run,turn) 表）
    dom = [t for t in turns if I(t["race_turn"]) == 0]
    chk("A6 域内 (run,turn) 数", len(dom), 477)
    chk("A6 比赛回合数", len(turns) - len(dom), 282)
    chk("A6 域内给了应然总数的行", sum(1 for t in dom if t["expected_slots"] != ""), 477)
    chk("A6 比赛回合不给数", sum(1 for t in turns if I(t["race_turn"]) == 1 and t["expected_slots"] != ""), 0)
    chk("A7 engine_applies==1", sum(1 for t in turns if I(t["engine_applies"]) == 1), 477)
    chk("A7 spec_complete==1", sum(1 for t in turns if I(t["spec_complete"]) == 1), 413)
    chk("A8 域内 应然总数==实测总数(801 列)",
        sum(1 for t in dom if I(t["expected_slots"]) == I(t["sub_len_801_measured"])), 477)
    # 板表的两个实测列母体不同：`sub_len_board_measured` 含**非 801 源**的分身，
    # 所以与「801 线应然」只在「本回合除 801 外没有别的来源」时相等；和全源应然比才是严判。
    diff_board = [t for t in dom if I(t["expected_slots"]) != I(t["sub_len_board_measured"])]
    show("A8 应然(801线) != 全板实测 的域内行数", len(diff_board))
    strict_ok = strict_bad = 0
    strict_keys = []
    for t in dom:
        if t["expected_slots_all_src"] == "":
            continue
        if I(t["expected_slots_all_src"]) == I(t["sub_len_board_measured"]):
            strict_ok += 1
        else:
            strict_bad += 1
            strict_keys.append((t["run_id"], t["turn_panel"]))
    chk("A8 全源应然可比行数 == spec_complete 行数", strict_ok + strict_bad, 413)
    show("A8 全源口径吻合行数", strict_ok)
    # 严判只应该漏在**已登记的外推例外**上：全源应然 != 全板实测 的三行，
    # 必须逐键等于 §D 的 S4/R7 例外清单（run179 t75 / run201 t73 / run201 t77）。
    chk("A8 全源严判的漏点 == 文档登记的 3 例外", sorted(strict_keys),
        sorted([("179", "75"), ("201", "73"), ("201", "77")]))
    no_other_src = [t for t in diff_board
                    if not any(I(c["on_board"]) == 1 and (I(c["sfuse"]) == 1 or I(c["sfin"]) == 1)
                               for c in by_key[(t["run_id"], t["turn_panel"])])]
    chk("A8 差值行都能由「本回合另有非 801 源」解释", len(no_other_src), 0)

    # 求和一致性：卡表 → 板表（三列各自的母体要各自比）
    bad_801_exp = bad_all_exp = bad_board_meas = bad_801_meas = 0
    for t in turns:
        g = by_key[(t["run_id"], t["turn_panel"])]
        if sum(I(c["sub_len_measured"]) for c in g if I(c["s801"]) == 1) != I(t["sub_len_801_measured"]):
            bad_801_meas += 1
        if t["expected_slots"] != "":
            if sum(I(c["expected_slots"]) for c in g if I(c["s801"]) == 1
                   and c["expected_slots"] != "") != I(t["expected_slots"]):
                bad_801_exp += 1
        if t["expected_slots_all_src"] != "":
            if sum(I(c["expected_slots"]) for c in g if c["expected_slots"] != "") \
                    != I(t["expected_slots_all_src"]):
                bad_all_exp += 1
        if sum(I(c["sub_len_measured"]) for c in g) != I(t["sub_len_board_measured"]):
            bad_board_meas += 1
    chk("A9 801 卡应然求和==板应然(801线)", bad_801_exp, 0)
    chk("A9 全卡应然求和==板应然(全源)", bad_all_exp, 0)
    chk("A9 全卡实测求和==板实测(全板)", bad_board_meas, 0)

    # 逐源普查（§3 表），从卡表独立重数
    for tag, flag, dom_only_board in [("S1 801", "s801", True), ("S2 101本体", "s101", True),
                                      ("S3 融合", "sfuse", True), ("S4 末段", "sfin", True)]:
        pres = [c for c in cards if I(c[flag]) == 1]
        ob = [c for c in pres if I(c["on_board"]) == 1]
        ind = [c for c in ob if I(c["race_turn"]) == 0]
        iso = [c for c in ind if I(c["n_src"]) == 1]
        combo = [c for c in ind if I(c["n_src"]) > 1]
        chk("A10 %s 全量在场" % tag, len(pres), {"S1 801": 514, "S2 101本体": 219,
                                                 "S3 融合": 84, "S4 末段": 270}[tag])
        chk("A10 %s 有站位" % tag, len(ob), {"S1 801": 477, "S2 101本体": 196,
                                             "S3 融合": 84, "S4 末段": 270}[tag])
        chk("A10 %s 域内" % tag, len(ind), {"S1 801": 264, "S2 101本体": 161,
                                            "S3 融合": 68, "S4 末段": 135}[tag])
        chk("A10 %s 隔离" % tag, len(iso), {"S1 801": 216, "S2 101本体": 161,
                                            "S3 融合": 68, "S4 末段": 87}[tag])
        chk("A10 %s 复合" % tag, len(combo), {"S1 801": 48, "S2 101本体": 0,
                                              "S3 融合": 0, "S4 末段": 48}[tag])
    show("A11 S1 有站位−域内 = #51 的比赛回合数", 477 - 264, "want 213")
    chk("A11 S1 域外有站位", len([c for c in cards if I(c["s801"]) == 1 and I(c["on_board"]) == 1
                                 and I(c["race_turn"]) == 1]), 213)
    chk("A11 S1 在场但无站位（#51 的 37）", len([c for c in cards if I(c["s801"]) == 1
                                                 and I(c["on_board"]) == 0]), 514 - 477)
    chk("A12 域内「带 801 却无站位」", len([c for c in cards if I(c["s801"]) == 1
                                           and I(c["on_board"]) == 0 and I(c["race_turn"]) == 0]), 0)
    chk("A13 S1 复合样本全是 S1+S4 且 2 槽",
        len([c for c in cards if I(c["s801"]) == 1 and I(c["sfin"]) == 1 and I(c["race_turn"]) == 0
             and I(c["on_board"]) == 1 and I(c["sub_len_measured"]) == 2]), 48)
    chk("A13 全量 2 槽总数恰为 48", len([c for c in cards if I(c["sub_len_measured"]) == 2]), 48)
    chk("A13 槽数只取 0/1/2", sorted({I(c["sub_len_measured"]) for c in cards}), [0, 1, 2])

    # R4/R6 的表侧与域条件
    r4 = [c for c in cards if c["rule_id"] == "R4"]
    chk("A14 R4 的 race_entry_type 全 0", len({I(c["race_entry_type"]) for c in r4}), 1)
    show("A14 R4 的面板回合集合", sorted({I(c["turn_panel"]) for c in r4}))
    chk("A14 R4 覆盖 (run,turn) 数", len({(c["run_id"], c["turn_panel"]) for c in r4}), 27)
    r6 = [c for c in cards if c["rule_id"] == "R6"]
    chk("A14 R6 全部不在末段", sum(1 for c in r6 if I(c["sfin"]) == 1), 0)
    chk("A14 R5 无样本", len([c for c in cards if c["rule_id"] == "R5"]), 0)
    dom_fin = {(c["run_id"], I(c["turn_panel"])) for c in cards
               if I(c["sfin"]) == 1 and I(c["race_turn"]) == 0 and I(c["on_board"]) == 1
               and I(c["s801"]) == 0 and I(c["sfuse"]) == 0}
    chk("A15 末段域内 S4-only 覆盖 (run,turn) 数 = 27", len(dom_fin), 27)
    show("A15 涉及局数 / 面板回合", "%d 局 / %s" %
         (len({k[0] for k in dom_fin}), sorted({k[1] for k in dom_fin})))
    show("A15 涉及卡数", sorted({c["card_id"] for c in cards if (c["run_id"], I(c["turn_panel"])) in dom_fin}))
    fin_race = [t for t in turns if I(t["race_turn"]) == 0 and I(t["turn_panel"]) in (72, 74, 76, 78)]
    show("A16 面板 72/74/76/78 的域内行数（文档称全为比赛回合）", len(fin_race))
    chk("A16 末段 entry!=0 且域内 = 0 样本",
        len({(c["run_id"], c["turn_panel"]) for c in cards
             if I(c["sfin"]) == 1 and I(c["race_turn"]) == 0 and I(c["race_entry_type"]) != 0}), 0)

    # S3 / S4 例外清单
    s3 = [c for c in cards if I(c["sfuse"]) == 1 and I(c["race_turn"]) == 0 and I(c["on_board"]) == 1]
    s3ex = sorted("%s/t%s/%s/n%d" % (c["run_id"], c["turn_panel"], c["card_id"], I(c["sub_len_measured"]))
                  for c in s3 if I(c["sub_len_measured"]) != 1)
    show("A17 S3 隔离域内数", len(s3))
    chk("A17 S3 例外数", len(s3ex), 4)
    chk("A17 S3 例外清单", s3ex, sorted(["171/t61/30311/n0", "171/t62/30311/n0",
                                        "171/t63/30282/n0", "171/t64/30311/n0"]))
    chk("A17 S3 其余局样本全 1 槽",
        len([c for c in s3 if c["run_id"] != "171" and I(c["sub_len_measured"]) == 1]), 56)
    chk("A17 run171 域内 S3 样本", len([c for c in s3 if c["run_id"] == "171"]), 12)
    show("A17 run171 S3 逐(回合,卡)实测",
        sorted((I(c["turn_panel"]), c["card_id"], I(c["sub_len_measured"])) for c in s3 if c["run_id"] == "171"))
    s4 = [c for c in cards if I(c["sfin"]) == 1 and I(c["s801"]) == 0 and I(c["sfuse"]) == 0
          and I(c["race_turn"]) == 0 and I(c["on_board"]) == 1]
    s4ex = sorted("%s/t%s/%s/n%d" % (c["run_id"], c["turn_panel"], c["card_id"], I(c["sub_len_measured"]))
                  for c in s4 if I(c["sub_len_measured"]) != 1)
    chk("A18 S4 隔离域内数", len(s4), 87)
    chk("A18 S4 例外清单", s4ex, sorted(["179/t75/30289/n0", "201/t73/30289/n0", "201/t77/30289/n0"]))
    c289 = [c for c in cards if c["card_id"] == "30289" and I(c["sfin"]) == 1
            and I(c["race_turn"]) == 0 and I(c["s801"]) == 0 and I(c["sfuse"]) == 0]
    chk("A18 30289 域内 R7 样本", len(c289), 27)
    chk("A18 30289 可见/不可见", [len([c for c in c289 if I(c["sub_len_measured"]) == 1]),
                                  len([c for c in c289 if I(c["sub_len_measured"]) == 0])], [24, 3])
    chk("A18 30289 出现在全部 (run,turn)", len({(c["run_id"], c["turn_panel"]) for c in cards if c["card_id"] == "30289"}), 759)
    show("A18 run201 卡30289 末段逐回合",
        sorted((I(c["turn_panel"]), I(c["sub_len_measured"])) for c in c289 if c["run_id"] == "201"))
    chk("A19 域内有分身的卡回合数（§F 的 412）",
        len([c for c in cards if I(c["race_turn"]) == 0 and I(c["sub_len_measured"]) > 0]), 412)
    # §F／R9 落位形状。分隔符是 `|`；单位要分清：412 是**卡回合行**，文档那句「落某个 510x 347」
    # 数的是**分身标记**（299 个行里有 48 个带 2 条标记）⇒ 两种单位都列。
    clones = [c for c in cards if I(c["race_turn"]) == 0 and I(c["sub_len_measured"]) > 0]

    def ids(s):
        return [int(x) for x in (s or "").split("|") if x.strip().isdigit()]
    chk("A20 分身与本体同一条目 = 0（R9）",
        len([c for c in clones if set(ids(c["body_entry_ids"])) & set(ids(c["clone_entry_ids"]))]), 0)
    chk("A20 `sub_len_measured` == 登记分身条目数（逐行自洽）",
        len([c for c in clones if len(ids(c["clone_entry_ids"])) != I(c["sub_len_measured"])]), 0)
    chk("A20 本体没落条目却当分身 = §F 的 12",
        len([c for c in clones if not ids(c["body_entry_ids"])]), 12)
    chk("A20 落 510x 的**分身标记**数 == §F 的 347",
        sum(1 for c in clones for e in ids(c["clone_entry_ids"]) if 5101 <= e <= 5105), 347)
    show("A20 落 510x 的**卡回合**数（与 347 同行不同单位）",
         len([c for c in clones if any(5101 <= e <= 5105 for e in ids(c["clone_entry_ids"]))]))
    show("A20 含 10x（普通训练位）的卡回合数",
         len([c for c in clones if any(e < 1000 for e in ids(c["clone_entry_ids"]))]))
    chk("A20 没有一行同时落 510x 与 10x",
        len([c for c in clones if any(5101 <= e <= 5105 for e in ids(c["clone_entry_ids"]))
             and any(e < 1000 for e in ids(c["clone_entry_ids"]))]), 0)
    return by_key


# ---------------------------------------------------------------- B 段
def part_b(cards, turns):
    print("\n===== B. master.mdb 表侧出处 =====")
    c = sqlite3.connect("file:%s?mode=ro" % MDB.replace("\\", "/"), uri=True)
    q = lambda s, a=(): [dict(zip([d[0] for d in cur.description], r))
                         for cur in [c.execute(s, a)] for r in cur.fetchall()]
    e18 = q("select * from single_mode_15_advice_effect where effect_type=18")
    chk("B1 effect_type=18 的 advice_id 只有 801", sorted({r["advice_id"] for r in e18}), [801])
    show("B1 行数 / value_2 / value_4", "%d / %s / %s" %
         (len(e18), sorted({r["effect_value_2"] for r in e18}), sorted({r.get("effect_value_4") for r in e18})))
    show("B1 涉及的 Lv", "%s..%s" % (min(r["level"] for r in e18), max(r["level"] for r in e18)))
    a101 = q("select distinct effect_type from single_mode_15_advice_effect where advice_id=101")
    chk("B2 牵绊 101 无 effect_type 18", sorted(r["effect_type"] for r in a101), [13, 16])
    fu = q("select * from single_mode_15_fusion_effect where id=2")
    show("B3 fusion id2", [(r["advice_id"], r["effect_type"], r["effect_value_4"]) for r in fu])
    chk("B3 fusion id2 = advice101 / type17 / v4=1",
        [(r["advice_id"], r["effect_type"], r["effect_value_4"]) for r in fu], [(101, 17, 1)])
    fa = q("select * from single_mode_15_finals_act_effect where id=4")
    show("B4 finals_act id4", [(r["effect_type"], r["effect_value_4"], r["condition_type"]) for r in fa])
    chk("B4 finals id4 = type111 / v4=1 / condition_type=0",
        [(r["effect_type"], r["effect_value_4"], r["condition_type"]) for r in fa], [(111, 1, 0)])
    show("B4 finals_act 全表 condition_type 取值",
         sorted({r["condition_type"] for r in q("select * from single_mode_15_finals_act_effect")}))
    fin = {r[0] for r in c.execute("select turn from single_mode_turn where turn_set_id=15 and period=3")}
    tt = {r[0]: (r[1], r[2]) for r in
          c.execute("select turn, period, race_entry_type from single_mode_turn where turn_set_id=15")}
    c.close()
    show("B5 period=3 回合集合", sorted(fin))
    csv_fin = {I(x["turn_panel"]) for x in cards if I(x["sfin"]) == 1}
    chk("B5 CSV 的 sfin 回合 == 表侧 period=3", sorted(csv_fin), sorted(fin))
    chk("B6 末段行全部落在 period=3",
        len([x for x in cards if I(x["sfin"]) == 1 and I(x["turn_panel"]) not in fin]), 0)
    race_by_turn = collections.defaultdict(int)
    for t in turns:
        if I(t["race_turn"]) == 1:
            race_by_turn[I(t["turn_panel"])] += 1
    show("B7 末段各面板回合的「有比赛」局数",
         {t: (race_by_turn.get(t, 0), len({x['run_id'] for x in cards if I(x['turn_panel']) == t})) for t in sorted(fin)})
    show("B7 末段表侧 race_entry_type", {t: tt[t][1] for t in sorted(fin)})
    chk("B8 末段 entry!=0 的回合都出现过比赛",
        sorted(t for t in fin if tt[t][1] != 0 and race_by_turn.get(t, 0) == 0), [])
    show("B9 域内末段面板回合（entry=0 且真的没比赛）",
         sorted(t for t in fin if race_by_turn.get(t, 0) < len({x['run_id'] for x in cards if I(x['turn_panel']) == t})))


# ---------------------------------------------------------------- C 段
def part_c(by):
    print("\n===== C. 报文独立复算（自己走，不用 clone_visibility） =====")
    boards = collections.defaultdict(list)   # (run, turn) -> [(idx, ps, data, ci)]
    sub_all = dict(tot=0, not_in_partners=0, ge100=0, dup=0)
    sub_ps1 = dict(tot=0, not_in_partners=0, ge100=0, dup=0)
    n_files = n_dec = 0
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".bin"):
            continue
        n_files += 1
        try:
            d = ml.decode_file(RAW + f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        n_dec += 1
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or ci.get("scenario_id") != 15:
            continue
        hi = data.get("home_info")
        turn, run = ci.get("turn"), ci.get("single_mode_chara_id")
        if not isinstance(turn, int):
            continue
        ents = {e.get("command_id"): e for e in (hi or {}).get("command_info_array") or []
                if e.get("command_id") in PLAIN or e.get("command_id") in KEIKO}
        if isinstance(hi, dict) and hi.get("command_info_array") and ents:
            ps = ci.get("playing_state")
            boards[(run, turn)].append((n_files, ps, data, ci))
            for tally in [sub_all] + ([sub_ps1] if ps == 1 else []):
                for e in ents.values():
                    sb = [p for p in (e.get("sub_command_partner_array") or []) if isinstance(p, int)]
                    tp = {p for p in (e.get("training_partner_array") or []) if isinstance(p, int)}
                    tally["tot"] += len(sb)
                    tally["not_in_partners"] += len([p for p in sb if p not in tp])
                    tally["ge100"] += len([p for p in sb if p >= 100])
                    tally["dup"] += len(sb) - len(set(sb))
    show("C0 报文文件数 / 可解码 dict", "%d / %d" % (n_files, n_dec))
    show("C0 scen15 有板的 (run,turn) 数", len(boards))
    chk("C0 我的 (run,turn) 键集合 == CSV 的 759 行",
        sorted(boards.keys()), sorted((int(t["run_id"]), int(t["turn_panel"])) for t in TURN))
    ps1 = {k: v for k, v in boards.items() if any(x[1] == 1 for x in v)}
    show("C0 其中含 ps==1 板的 (run,turn)", len(ps1))
    no_ps1 = sorted(set(boards) - set(ps1))
    show("C0 没有任何 ps==1 板的 (run,turn)", no_ps1)
    for k in no_ps1:
        row = [t for t in TURN if (t["run_id"], t["turn_panel"]) == (str(k[0]), str(k[1]))]
        show("C0  → run%s t%s 的板 ps 序列 / 域 / 应然 / 实测" % k,
             "%s｜race=%s｜exp=%s｜board=%s｜801=%s" %
             ([x[1] for x in boards[k]], row[0]["race_turn"], row[0]["expected_slots"],
              row[0]["sub_len_board_measured"], row[0]["sub_len_801_measured"]) if row else "CSV 无此键")
    chk("C1 恒等式三条（全部板包，含非 ps==1）",
        "%d/%d/%d" % (sub_all["not_in_partners"], sub_all["ge100"], sub_all["dup"]), "0/0/0")
    show("C1 母体：全部板包 sub 元素", sub_all["tot"])
    chk("C1 恒等式三条（只 ps==1 板包）",
        "%d/%d/%d" % (sub_ps1["not_in_partners"], sub_ps1["ge100"], sub_ps1["dup"]), "0/0/0")
    chk("C1 母体：ps==1 板包 sub 元素 == 文档 §1 的 1,075", sub_ps1["tot"], 1075)
    # 决策板：最后一条 ps==1，优先不带 command_result
    dec = {}
    for k, v in boards.items():
        c1 = [x for x in v if x[1] == 1]
        if not c1:
            continue
        c2 = [x for x in c1 if not isinstance(x[2].get("command_result"), dict)] or c1
        dec[k] = c2[-1]
    show("C2 决策板数", len(dec))
    dec_tot = 0
    for (run, turn), rec in sorted(dec.items()):
        ents = board_ents(rec[2])
        dec_tot += sum(len([p for p in (e.get("sub_command_partner_array") or [])
                            if isinstance(p, int)]) for e in ents.values())
    show("C2 只算 756 块决策板的 sub 元素（口径不同，供后续反查）", dec_tot)
    mismatch = 0
    checked = 0
    for (run, turn), rec in sorted(dec.items()):
        data = rec[2]
        ci = data.get("chara_info") or {}
        deck = {s.get("position"): s.get("support_card_id")
                for s in (ci.get("support_card_array") or [])}
        ents = {e.get("command_id"): e for e in (data.get("home_info") or {}).get("command_info_array") or []
                if e.get("command_id") in PLAIN or e.get("command_id") in KEIKO}
        clone_cnt = collections.Counter()
        board_cnt = 0
        for e in ents.values():
            sb = {p for p in (e.get("sub_command_partner_array") or []) if isinstance(p, int)}
            board_cnt += len(sb)
            for p in sb:
                clone_cnt[p] += 1
        key = (str(run), str(turn))
        g = by.get(key)
        if g is None:
            continue
        for c in g:
            pos = I(c["pos"])
            want = I(c["sub_len_measured"])
            got = clone_cnt.get(pos, 0)
            checked += 1
            if got != want:
                mismatch += 1
                if mismatch <= 8:
                    show("C3 违例", "run%s t%s pos%s card%s CSV=%d 报文=%d" %
                         (run, turn, pos, c["card_id"], want, got))
        trow = [t for t in TURN if (t["run_id"], t["turn_panel"]) == key][0]
        if I(trow["sub_len_board_measured"]) != board_cnt and I(trow["race_turn"]) == 0:
            mismatch += 1
            if mismatch <= 12:
                show("C4 板级合计违例", "run%s t%s CSV=%s 报文=%d" %
                     (run, turn, trow["sub_len_board_measured"], board_cnt))
    chk("C3 逐卡实测列 == 报文重数（比对行）", mismatch, 0)
    show("C3 比对到的卡回合行数", checked)


TURN = []


def main():
    global TURN
    only = sys.argv[1] if len(sys.argv) > 1 else "abc"
    turns = read_csv(TURN_CSV)
    cards = read_csv(CARD_CSV)
    TURN = turns
    by = {}
    if "a" in only or "c" in only:
        by = part_a(turns, cards)
    if "b" in only:
        part_b(cards, turns)
    if "c" in only:
        part_c(by)
    print("\n===== 结论 =====")
    if fails:
        print("!! %d 条复算不一致：%s" % (len(fails), fails[:20]))
        return 1
    print("全部复算一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
