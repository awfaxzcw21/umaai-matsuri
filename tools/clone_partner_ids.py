"""分身（`sub_command_partner_array`）的 partner id 级抽取：来源与落点（#47）。

`tools/train_leg_preview.py:79-90` 只留了计数（`heads_sub ⊆ heads_deck`），这里把**具体卡组站位**
还原成卡 id，并与同一条报文里的教诲 / 合宿 / 演目状态对表，回答 #46 §5 的三个问题。

    python tools/clone_partner_ids.py            # 重跑全量报文，出 CSV + 下面所有表

口径全部走 master.mdb（表侧优先），脚本里不写死：
  得意训练位 = `support_card_data.command_id`；`sub_command_partner_array` 里是**卡组站位序号**
  （`chara_info.support_card_array.position`），不是卡 id；站位→卡→`chara_id`→
  `festa_data_set.evaluation_info_array[].act_type` 才是该卡的担当演目；
  演目↔命令 = `single_mode_15_act_info`；510x 的实际训练类型 = `single_mode_training.base_command_id`。
只读：不改任何 tracked 文件、不写报文目录。
"""

import argparse
import collections
import csv
import os
import sqlite3
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
PREVIEW = "out/train_leg_preview_20261103.csv"
OUT = "out/clone_partners_20261104.csv"
PLAIN = {101, 102, 103, 105, 106}      # 板上实际出现的 5 个普通训练位（104 在报文里从不出现）
KEIKO = {5101, 5102, 5103, 5104, 5105}
TN = {101: "速", 102: "力", 103: "根", 105: "耐", 106: "智", 0: "友/团"}
ADVICE_NAME = {101: "牵绊", 201: "擅长率", 301: "灵感", 401: "助力", 501: "体力",
               601: "特化", 701: "研钻", 801: "协助", 901: "不屈", 1001: "兼修"}
SRC_801 = 801          # 协助教诲（融合体自身的 advice_id）
SRC_BOND = 101         # 牵绊：融合后作为 fusion_advice_id 出现，合宿期内给分身
TURN_SET_15 = 15       # `single_mode_turn.turn_set_id`：剧本 15 自己的回合表
FINALS_PERIOD = 3      # `single_mode_turn.period` 里末段（总决赛）那一段的取值


def load_tables(mdb):
    """master.mdb 只读打开。缺表直接报错，不静默回退到猜测。"""
    c = sqlite3.connect("file:%s?mode=ro" % mdb.replace("\\", "/"), uri=True)
    deyi = {r[0]: r[1] for r in c.execute("select id, command_id from support_card_data")}
    stype = {r[0]: r[1] for r in c.execute("select id, support_card_type from support_card_data")}
    chara = {r[0]: r[1] for r in c.execute("select id, chara_id from support_card_data")}
    a2cmd = {r[0]: r[1] for r in c.execute(
        "select act_type, command_id from single_mode_15_act_info")}
    base = {r[0]: r[1] for r in c.execute(
        "select distinct command_id, base_command_id from single_mode_training "
        "where command_id between 5101 and 5105")}
    fin = {r[0] for r in c.execute(
        "select turn from single_mode_turn where turn_set_id=? and period=?",
        (TURN_SET_15, FINALS_PERIOD))}
    c.close()
    return deyi, stype, chara, a2cmd, base, fin


def read_board(data, chara):
    """一条板报文 → 站位 / 演目 / 教诲状态 + 每个训练条目的分身标记。"""
    ci = data.get("chara_info") or {}
    fds = data.get("festa_data_set") or {}
    fi = fds.get("festa_info") or {}
    n = fi.get("summer_camp_remain_turn_num")
    deck = {s.get("position"): s.get("support_card_id")
            for s in (ci.get("support_card_array") or [])}
    acts = {a.get("act_type"): a for a in (fds.get("act_info_array") or [])}
    entry = {}
    for c in ((data.get("home_info") or {}).get("command_info_array") or []):
        cid = c.get("command_id")
        if cid in PLAIN or cid in KEIKO:
            entry[cid] = c
    stand = collections.defaultdict(list)   # pos -> [(cid, is_clone, in_tips)]
    for cid, c in entry.items():
        ps = [p for p in (c.get("training_partner_array") or []) if isinstance(p, int)]
        sb = {p for p in (c.get("sub_command_partner_array") or []) if isinstance(p, int)}
        tp = {p for p in (c.get("tips_event_partner_array") or []) if isinstance(p, int)}
        for p in ps:
            stand[p].append((cid, p in sb, p in tp))
    return dict(turn=ci.get("turn"), run=ci.get("single_mode_chara_id"),
                start=ci.get("start_time"), camp=1 if isinstance(n, int) and n > 0 else 0,
                step=fi.get("act_training_step"), deck=deck, acts=acts,
                act_adv={k: v.get("advice_id") for k, v in acts.items()},
                adv_base={a.get("advice_id"): a.get("fusion_advice_id")
                          for a in (fds.get("advice_info_array") or [])},
                chara_act={e.get("chara_id"): e.get("act_type")
                           for e in (fds.get("evaluation_info_array") or [])},
                entry=entry, stand=stand)


def card_state(b, pos, tbl):
    """一张卡组站位在这块板上的全部可判读属性（来源 / 落点 / 担当演目状态）。"""
    deyi, stype, chara, a2cmd, base, fin = tbl
    cid = b["deck"].get(pos)
    act = b["chara_act"].get(chara.get(cid))
    a = b["acts"].get(act) or {}
    A = a.get("advice_id")
    B = b["adv_base"].get(A, A)
    need = a.get("need_act_gauge")
    lit = 1 if need and (a.get("act_gauge") or 0) >= need else 0
    es = b["stand"][pos]
    mk = [c for c, s, _ in es if s]
    body = [c for c, s, _ in es if not s]
    src801 = 1 if A == SRC_801 else 0
    srcbond = 1 if (B == SRC_BOND and A != SRC_BOND and b["camp"]) else 0
    return dict(pos=pos, card=cid, ctype=stype.get(cid), deyi=deyi.get(cid), act=act,
                adv_obj=A, adv_base=B, lit=lit, keiko=a2cmd.get(act),
                state=a.get("act_training_state"), gauge=a.get("act_gauge"), need=need,
                act_lv=a.get("level"), mk=mk, body=body, src801=src801, srcbond=srcbond,
                src_adv=src801 + srcbond, src_fin=1 if (b["turn"] or 0) in fin else 0,
                on_board=1 if es else 0)


def scan(args):
    tbl = load_tables(args.mdb)
    deyi, stype, chara, a2cmd, base, fin = tbl
    cmd2act = {v: k for k, v in a2cmd.items()}
    with open(args.preview, encoding="utf-8-sig") as fh:
        prev = {(r["file"], r["turn"], r["cid"]): r["heads_sub"]
                for r in csv.DictReader(fh)}

    rows = []
    boards = {}
    st = collections.Counter()
    for f in sorted(os.listdir(args.raw)):
        if not f.endswith(".bin"):
            continue
        try:
            d = ml.decode_file(args.raw + f)
        except Exception:
            continue
        data = d.get("data") if isinstance(d, dict) else None
        if not isinstance(data, dict):
            continue
        b = read_board(data, chara)
        if not isinstance(b["turn"], int) or not b["entry"]:
            continue
        st["msg_boards"] += 1
        boards[(b["run"], b["start"], b["turn"])] = b      # 同回合取最后一条报文
        for cid, c in b["entry"].items():
            ps = [p for p in (c.get("training_partner_array") or []) if isinstance(p, int)]
            srl = [p for p in (c.get("sub_command_partner_array") or []) if isinstance(p, int)]
            sb = set(srl)
            nsub = sum(1 for p in ps if p < 100 and p in sb)
            st["sub_ge100"] += sum(1 for p in srl if p >= 100)
            st["sub_not_in_partners"] += sum(1 for p in srl if p not in ps)
            st["dup_deck_partner"] += 1 if len([p for p in ps if p < 100]) != \
                len({p for p in ps if p < 100}) else 0
            st["dup_any_partner"] += 1 if len(ps) != len(set(ps)) else 0
            key = (f, str(b["turn"]), str(cid))
            if key in prev:
                st["csv_ok" if prev[key] == str(nsub) else "csv_BAD"] += 1
            else:
                st["csv_missing"] += 1
            for p in sorted(sb):
                if not isinstance(p, int) or p >= 100 or p not in b["deck"]:
                    continue
                k = card_state(b, p, tbl)
                ttype = base.get(cid, cid)
                rows.append({
                    "file": f, "run": b["run"], "turn": b["turn"], "cid": cid,
                    "kind": "keiko" if cid in KEIKO else "plain",
                    "target_train": ttype, "target_train_name": TN.get(ttype, "?"),
                    "target_act_of_keiko": cmd2act.get(cid, 0),
                    "pos": p, "card_id": b["deck"][p], "card_mdb_type": k["ctype"],
                    "deyi_cmd": k["deyi"], "deyi_name": TN.get(k["deyi"], "?"),
                    "on_deyi": 1 if ttype == k["deyi"] else 0,
                    "on_own_act_keiko": 1 if cid == k["keiko"] else 0,
                    "on_body_type": 1 if ttype in {base.get(x, x) for x in k["body"]} else 0,
                    "body_cids": "|".join(str(x) for x in sorted(k["body"])),
                    "body_types": "|".join(str(base.get(x, x)) for x in sorted(k["body"])),
                    "own_act": k["act"], "own_act_lit": k["lit"], "own_act_state": k["state"],
                    "own_act_gauge": k["gauge"], "own_act_need": k["need"],
                    "own_act_lv": k["act_lv"],
                    "advice_obj": k["adv_obj"], "advice_base": k["adv_base"],
                    "advice_name": ADVICE_NAME.get(k["adv_obj"], str(k["adv_obj"])),
                    "src_801": k["src801"], "src_bond_fused_camp": k["srcbond"],
                    "src_advice_total": k["src_adv"], "src_finals_turn_window": k["src_fin"],
                    "n_marked_this_card_turn": len(k["mk"]),
                    "n_body_this_card_turn": len(k["body"]),
                    "camp": b["camp"], "act_training_step": b["step"],
                    "train_lv": c.get("level"),
                    "tips_here": 1 if p in (c.get("tips_event_partner_array") or []) else 0,
                    "heads_sub_entry": nsub, "csv_heads_sub": prev.get(key, ""),
                    "csv_match": "" if key not in prev else int(prev[key] == str(nsub)),
                    "in_preview_csv": 1 if key in prev else 0,
                })
                st["marker_rows"] += 1
    return rows, boards, dict(st), tbl


def cardturns(boards, tbl, types=(1,)):
    """去重到「唯一板快照 × 卡组站位」，含完全不在板上的站位（分身可能单独出现）。"""
    out = []
    for b in sorted(boards.values(), key=lambda x: (x["run"], x["turn"])):
        for pos in b["deck"]:
            if not isinstance(pos, int) or pos >= 100:
                continue
            k = card_state(b, pos, tbl)
            if k["ctype"] not in types:
                continue
            k["run"], k["turn"], k["camp"], k["step"] = b["run"], b["turn"], b["camp"], b["step"]
            out.append(k)
    return out


def tables(rows, boards, st, tbl):
    base, fin = tbl[4], tbl[5]
    ct = cardturns(boards, tbl)
    allct = cardturns(boards, tbl, types=(1, 2, 3, None))

    print("\n## 0. 抽取自检")
    print("  板报文 %d 条 → 唯一板快照 %d｜分身标记行（报文级）%d｜卡回合（唯一板×普通卡组卡）%d" % (
        st.get("msg_boards", 0), len(boards), st.get("marker_rows", 0), len(ct)))
    print("  sub 里出现 >=100 的 id %d 次｜sub 不在 partners 里 %d 次｜卡组人头重复 %d 条｜"
          "整个 partners 有重复 %d 条" % (
              st.get("sub_ge100", 0), st.get("sub_not_in_partners", 0),
              st.get("dup_deck_partner", 0), st.get("dup_any_partner", 0)))
    print("  回对 train_leg_preview CSV：可比 %d、一致 %d、不一致 %d、CSV 里没这条目 %d"
          "（CSV 只收 params_inc_dec_info_array 里 target_type=30 非空的条目）" % (
              st.get("csv_ok", 0) + st.get("csv_BAD", 0), st.get("csv_ok", 0),
              st.get("csv_BAD", 0), st.get("csv_missing", 0)))

    print("\n## 1. 落点：是否总在该卡「非得意」的训练类型")
    n_on = sum(1 for r in rows if r["on_deyi"])
    print("  标记 %d 条｜落在自己得意训练类型 %d（%.2f%%）｜落在自己担当演目的 510x %d｜"
          "落在「本体已站着的同一训练类型」%d" % (
              len(rows), n_on, 100.0 * n_on / max(len(rows), 1),
              sum(1 for r in rows if r["on_own_act_keiko"]),
              sum(1 for r in rows if r["on_body_type"])))
    for lab, sel in (("本体在场（有未标记条目）", lambda r: r["n_body_this_card_turn"] > 0),
                     ("本体不在场（只有标记）", lambda r: r["n_body_this_card_turn"] == 0)):
        g = [r for r in rows if sel(r)]
        print("    %-22s 标记%5d  落得意%4d (%.2f%%)  落本体同类型%4d" % (
            lab, len(g), sum(1 for r in g if r["on_deyi"]),
            100.0 * sum(1 for r in g if r["on_deyi"]) / max(len(g), 1),
            sum(1 for r in g if r["on_body_type"])))
    print("  得意命中的反例（最多列 40 条）：")
    for r in [x for x in rows if x["on_deyi"]][:40]:
        print("    run%-5s t%-3s pos%s card%-6s 得意%s 落点%s(%s) 本体[%s] own_act%s lit%s 教诲%s" % (
            r["run"], r["turn"], r["pos"], r["card_id"], r["deyi_name"], r["cid"],
            r["target_train_name"], r["body_cids"], r["own_act"], r["own_act_lit"],
            r["advice_name"]))
    mt = collections.Counter(r["target_train"] for r in rows)
    bt = collections.Counter(base.get(x, x) for k in ct if k["src_adv"] or k["src_fin"]
                             for x in k["body"])
    print("  落点训练类型分布 %s" % {TN.get(a, a): b for a, b in sorted(mt.items())})
    print("  本体训练类型分布（唯一板、有来源的卡回合）%s"
          % {TN.get(a, a): b for a, b in sorted(bt.items())})

    print("\n## 2. 每卡每回合的分身份数 vs +1 来源数")
    for lab, f in (("教诲可见来源(801+融合牵绊)", lambda k: k["src_adv"]),
                   ("再加末段窗口(period=%d)" % FINALS_PERIOD, lambda k: k["src_adv"] + k["src_fin"])):
        c = collections.Counter((f(k), len(k["mk"])) for k in ct)
        print("  %-30s 违例(分身>来源)=%d 无来源却有分身=%d  %s" % (
            lab, sum(v for (s, n), v in c.items() if n > s),
            sum(v for (s, n), v in c.items() if s == 0 and n), dict(sorted(c.items()))))
    c = collections.Counter((k["src_adv"] + k["src_fin"], k["on_board"], len(k["mk"])) for k in ct)
    print("  (来源数, 本体在板?, 分身槽数)：")
    for key in sorted(c, key=str):
        print("     src%d on_board%d mk%d : %d" % (key[0], key[1], key[2], c[key]))
    have_src = [k for k in ct if k["src_adv"] + k["src_fin"] >= 1]
    nomark = [k for k in have_src if not k["mk"]]
    print("  有来源卡回合 %d，其中 0 个可见分身槽 %d = %.2f%%" % (
        len(have_src), len(nomark), 100.0 * len(nomark) / max(len(have_src), 1)))
    print("     这批 0 槽卡回合：own_act_lit %s｜本体所在类型 %s" % (
        collections.Counter(k["lit"] for k in nomark),
        collections.Counter("|".join(str(base.get(x, x)) for x in k["body"]) for k in nomark)))
    off = sum(1 for k in ct if not k["on_board"])
    print("  基线不在场率：卡回合 %d，完全不在板 %d = %.2f%%" % (
        len(ct), off, 100.0 * off / max(len(ct), 1)))
    print("  卡类型 × 分身（support_card_type 1 普通 / 2 友人 / 3 团队）：")
    for t in sorted({k["ctype"] for k in allct}, key=lambda v: (v is None, v)):
        g = [k for k in allct if k["ctype"] == t]
        print("     type=%s 卡回合%6d  有分身%5d  槽%5d" % (
            t, len(g), sum(1 for k in g if k["mk"]), sum(len(k["mk"]) for k in g)))
    npc = collections.Counter()
    for b in boards.values():
        for c in b["entry"].values():
            for p in (c.get("sub_command_partner_array") or []):
                npc["npc_ge100" if isinstance(p, int) and p >= 100 else "deck_lt100"] += 1
    print("  sub 数组 id 构成（唯一板）：%s" % dict(npc))

    print("\n## 3. 来源 × 回合：t27-39 那 3.5% 是谁给的")
    per = collections.defaultdict(lambda: collections.Counter())
    campturn = {b["turn"] for b in boards.values() if b["camp"]}
    for k in ct:
        t = k["turn"]
        per[t]["卡回合"] += 1
        per[t]["槽"] += len(k["mk"])
        if k["src801"]:
            per[t]["801"] += len(k["mk"])
        if k["srcbond"]:
            per[t]["牵绊融合"] += len(k["mk"])
        if not k["src_adv"] and k["mk"]:
            per[t]["无可见来源"] += len(k["mk"])
    print("  %-6s %6s %5s %5s %8s %8s %6s" % ("turn", "卡回合", "槽", "801", "牵绊融合", "无来源", "合宿"))
    for t in sorted(per):
        v = per[t]
        if not v["槽"]:
            continue
        print("  %-6d %6d %5d %5d %8d %8d %6s" % (
            t, v["卡回合"], v["槽"], v["801"], v["牵绊融合"], v["无可见来源"],
            "camp" if t in campturn else ""))
    seg = collections.defaultdict(lambda: [0, 0])
    for k in ct:
        s = (k["turn"] - 1) // 13
        seg[s][0] += 1
        seg[s][1] += len(k["mk"])
    print("  回合段卡回合数 %s" % dict(sorted((s, v[0]) for s, v in seg.items())))
    print("  回合段分身槽数 %s" % dict(sorted((s, v[1]) for s, v in seg.items())))

    print("\n## 4. 末段 +1 的形状（报文里没有开关字段，窗口取自 single_mode_turn.period）")
    on = collections.defaultdict(list)
    for k in ct:
        if not k["src_adv"] and k["mk"]:
            on[k["run"]].append(k["turn"])
    for r in sorted(on):
        print("     run %-6s 无可见来源的分身回合 %s" % (r, sorted(set(on[r]))))
    reach = sorted({b["run"] for b in boards.values() if b["turn"] in fin})
    print("  到达末段（period=%d，回合 %s）的 run：%s｜上面列出的 run：%s" % (
        FINALS_PERIOD, sorted(fin), reach, sorted(on)))
    print("  末段内「教诲来源+末段」两来源卡回合的槽数：%s" % collections.Counter(
        len(k["mk"]) for k in ct if k["src_adv"] and k["src_fin"]))
    print("  末段全部卡回合的槽数：%s" % collections.Counter(
        len(k["mk"]) for k in ct if k["src_fin"]))

    print("\n## 5. 负对照：合宿里「未融合的牵绊 101」")
    hit = collections.Counter()
    turns = collections.defaultdict(set)
    for b in boards.values():
        if not b["camp"]:
            continue
        for pos in b["deck"]:
            if not isinstance(pos, int) or pos >= 100:
                continue
            k = card_state(b, pos, tbl)
            if k["ctype"] != 1:
                continue
            lab = "未融合101" if k["adv_obj"] == SRC_BOND else (
                "101已融合" if k["adv_base"] == SRC_BOND else "其它教诲")
            hit[(lab, len(k["mk"]))] += 1
            turns[lab].add(b["turn"])
    for key in sorted(hit, key=str):
        print("     %-10s 分身槽%d : %d 卡回合" % (key[0], key[1], hit[key]))
    print("     各标签出现的合宿回合：%s" % {k: sorted(v) for k, v in turns.items()})
    print("     报文里 camp=1 的回合集合：%s" % sorted(campturn))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=RAW)
    ap.add_argument("--mdb", default=MDB)
    ap.add_argument("--preview", default=PREVIEW)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    rows, boards, st, tbl = scan(a)
    if not rows:
        print("!! 没抽到任何分身标记行")
        return
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("分身明细（报文级，一行一个分身标记）%d 行 -> %s" % (len(rows), a.out))
    print("覆盖：%d 个 run｜回合 %d–%d" % (
        len({r["run"] for r in rows}), min(r["turn"] for r in rows), max(r["turn"] for r in rows)))
    tables(rows, boards, st, tbl)


if __name__ == "__main__":
    main()
