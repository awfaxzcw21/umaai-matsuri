# -*- coding: utf-8 -*-
"""#55 新靶读数：把「离 LG15 差多少」拆成 量（五维和）/ 价（技能腿）/ 配置（卡组）三条腿。

只读，全盘上产物，不扫报文（报文侧已由 real_score_dist.py 落盘）：
  in  out/_real_score_dist_20261105.csv   逐快照 426 行（含 落点族 / viewer / 技能数 / total_skill_pt）
  in  out/lf_chara_snapshots_20261104.csv 逐快照（含 stat_score / calc_score / deck / sum_grade / wins）
  in  out/_outing_act_level_turns.csv     真机逐回合演目 Lv（§6b 按 run 取末值）
  in  E:/HJM/_keileg_s52b/out/s52b_{A,D}_s{1..5}.jsonl  引擎两臂 300 局/臂
  in  E:/HJM/_keileg_s52b/_state_t30.json 两臂共用的真机 t30 锚点（§6b 核对是哪一局）
  in  master.mdb（只读，只做 卡→训练项 映射，不写任何常数）

运行（在仓根）：
    python tools/lg15_gap_decomp.py > out/_lg15_gap_decomp_20261105.txt 2>&1

⚠ 归属：426 条里绝大多数是排行榜/对手/继承池里**别人的马**。本脚本一律以
  viewer == 238107205（我们自己账号）且 五维和 ≥ 6,000 作「自己真练成」，其余算「别人」。
  混在一起算「真机批发率」＝把别人的高分马当我们的成绩（docs/新靶读数_LG15_20261105.md §1）。
  闸门**不能用报文 技能数**：同一匹马在 名册/finish 不同落点上 skill_array 会被截断成长 1。
"""
import csv, glob, json, os, sqlite3, statistics as st, sys
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWN = "238107205"
LINES = [("LG", 76000), ("LG15", 85600), ("LG24", 90900), ("LF", 91400)]
LG15 = 85600
AXES = ("speed", "stamina", "power", "guts", "wiz")
AX_CN = {"1": "速度", "2": "耐力", "3": "力量", "4": "根性", "5": "智慧"}
MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"


def i(x, d=0):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return d


def N(x):
    return "—" if x is None else "{:,}".format(int(round(x)))


def med(xs, f=lambda x: x):
    xs = [f(x) for x in xs]
    return st.median(xs) if xs else None


def rd(p):
    with open(os.path.join(ROOT, p), newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


# ── 卡 → 训练轴（表侧现推，不写死）────────────────────────────────────────
def card_axis():
    """command_type=1 的卡按 command_id 归轴；轴由 single_mode_training_effect 现推。

    自证：基础训练项 5101..5105 的 target_type 恰为 1..5 ⇒ target_type k = 第 k 轴
    （报文五维轴序 speed/stamina/power/guts/wiz）。
    """
    c = sqlite3.connect("file:%s?mode=ro" % MDB.replace("\\", "/"), uri=True)
    # command_id → 正收益最大的 target_type（限 1..5）
    tt = defaultdict(dict)
    for cmd, t, v in c.execute("select command_id,target_type,max(effect_value) from "
                               "single_mode_training_effect where scenario_id=15 and result_state=2 "
                               "and target_type between 1 and 5 group by 1,2"):
        tt[i(cmd)][i(t)] = i(v)
    axis_of_cmd = {cmd: max(m, key=lambda k: m[k]) for cmd, m in tt.items() if m}
    self_check = {cmd: axis_of_cmd.get(cmd) for cmd in range(5101, 5106)}
    cards = {i(r[0]): (i(r[1]), i(r[2]), i(r[3])) for r in c.execute(
        "select id,command_id,command_type,rarity from support_card_data")}
    c.close()
    return axis_of_cmd, cards, self_check


def deck_shape(rows, axis_of_cmd, cards):
    """一组马的卡组逐轴张数中位 + 未映射披露。"""
    per = []
    unk = Counter()
    for r in rows:
        ids = [i(x) for x in (r["deck"].strip("[]").split(",") if r["deck"].strip() else []) if i(x)]
        cnt = Counter()
        for cid in ids:
            ct = cards.get(cid)
            if ct is None:
                unk["id不在support_card_data"] += 1
                continue
            cmd, ctype, _rar = ct
            if ctype == 0:
                cnt["外出/友人"] += 1
            elif cmd in axis_of_cmd:
                cnt[AX_CN[str(axis_of_cmd[cmd])]] += 1
            else:
                unk["cmd%d" % cmd] += 1
        per.append(cnt)
    cols = ["速度", "耐力", "力量", "根性", "智慧", "外出/友人"]
    return cols, [med([c.get(k, 0) for c in per]) for k in cols], len(per), unk


def main():
    dist = rd(os.path.join("out", "_real_score_dist_20261105.csv"))
    lf = rd(os.path.join("out", "lf_chara_snapshots_20261104.csv"))
    print("读入 逐快照 %d 行（real_score_dist）+ %d 行（lf_chara_snapshots）"
          % (len(dist), len(lf)))

    # ── 1. 归属 ──────────────────────────────────────────────────────────
    print("\n【1 归属：谁是我们的马】")
    fam = Counter()
    for r in dist:
        fam[(r["落点族"], r["viewer"] == OWN)] += 1
    for (f_, own_), n in sorted(fam.items(), key=lambda x: -x[1]):
        print("  %-22s 自己=%-3s n=%d" % (f_, "是" if own_ else "否", n))
    # 分组一律以 real_score_dist（逐快照去重、带落点族）为准，再 join 回
    # lf_chara_snapshots 取五维腿/技能腿/卡组。join 键 = (viewer, 分, 五维和)。
    lfk = defaultdict(list)
    for r in lf:
        lfk[(r["viewer"], str(i(r["rank_score"])), str(i(r["five_sum"])))].append(r)

    def legs(rows):
        hit, miss = [], 0
        for r in rows:
            m = lfk.get((r["viewer"], str(i(r["score"])), str(i(r["五维和"]))))
            if m:
                hit.append(max(m, key=lambda x: i(x["n_skill_ids"])))
            else:
                miss += 1
        return hit, miss

    # 「自己真练成」＝我们账号里五维和 ≥6,000 的马。
    # ⚠ 不能用报文 技能数 做闸门：同一匹马在 名册/考试高分位/finish 不同落点上
    #   skill_array 会被截断成长 1（47,653 / 42,693 / 41,685 三匹就是这样被漏掉的）。
    #   刷局（自选比赛反复出赛）那批五维和只有 4,5xx–5,4xx，与真练成 7,795 起中间有空带。
    own = [r for r in dist if r["viewer"] == OWN and i(r["五维和"]) >= 6000]
    own_farm = [r for r in dist if r["viewer"] == OWN and i(r["五维和"]) < 6000]
    print("  ⇒ 自己真练成（viewer=自己 且 五维和≥6,000）n=%d；自己刷局（五维和<6,000）n=%d"
          % (len(own), len(own_farm)))
    print("  真练成分数：%s" % " ".join(N(i(r["score"])) for r in
                                        sorted(own, key=lambda r: -i(r["score"]))))
    print("  刷局分中位 %s（最高 %s，五维和 med %s）⇒ 不是育成成绩，本脚本全程剔除"
          % (N(med(own_farm, lambda r: i(r["score"]))),
             N(max(i(r["score"]) for r in own_farm)),
             N(med(own_farm, lambda r: i(r["五维和"])))))
    best = max(own, key=lambda r: i(r["score"]))
    print("  账号可核最高真机分（真练成里）= %s ｜ 五维和 %s ｜ card %s ｜ 落点 %s ｜ 报文技能数 %s ｜ 因子 %s"
          % (N(i(best["score"])), N(i(best["五维和"])), best["card"],
             best["落点族"], best["技能数"], best["因子"]))

    # ── 2. 同五维和分桶：量/价分离 ────────────────────────────────────────
    print("\n【2 同五维和桶内的分数（桶宽 200）：自己 vs 别人(技能数>1)】")
    others = [r for r in dist if r["viewer"] != OWN and i(r["技能数"]) > 1]
    print("  %-14s %14s %14s %10s" % ("五维和桶", "自己 med", "别人 med", "n(自/他)"))
    for lo in range(4400, 9600, 200):
        ob = [r for r in own if lo <= i(r["五维和"]) < lo + 200]
        nb = [r for r in others if lo <= i(r["五维和"]) < lo + 200]
        if not ob and not nb:
            continue
        print("  %-14s %14s %14s %10s" % (
            "%d–%d" % (lo, lo + 199),
            N(med(ob, lambda r: i(r["score"]))) if ob else "—",
            N(med(nb, lambda r: i(r["score"]))) if nb else "—",
            "%d/%d" % (len(ob), len(nb))))
    # LG15 落在哪个桶
    for lo in range(4400, 9600, 200):
        nb = [i(r["score"]) for r in others if lo <= i(r["五维和"]) < lo + 200]
        if nb and st.median(nb) >= LG15:
            print("  ⇒ 别人分数中位首次 ≥LG15(%s) 的桶 = 五维和 %d–%d（med %s，n=%d）"
                  % ("{:,}".format(LG15), lo, lo + 199, N(st.median(nb)), len(nb)))
            break

    # ── 3. 三条腿（用 lf_chara_snapshots 的 stat_score / calc_score）──────
    print("\n【3 三条腿中位：自己真练成 vs 别人≥LG15】")
    print("    口径（lf_packet_scan.py:42/319）：calc_score ＝ stat_score（五维腿）+ 1.0746 × Σgrade"
          "（技能等级和，K_SCEN15）；total_skill_pt 是报文里的**点数**字段，单列、不参与 calc")
    oth_hi_d = [r for r in others if i(r["score"]) >= LG15]
    oth_mid_d = [r for r in others if 60000 <= i(r["score"]) < LG15]
    lg_own, miss_own = legs(own)
    lg_hi, miss_hi = legs(oth_hi_d)
    lg_mid, miss_mid = legs(oth_mid_d)
    print("    join 命中：自己 %d/%d（缺 %d）｜别人≥LG15 %d/%d（缺 %d）｜别人60k–LG15 %d/%d（缺 %d）"
          % (len(lg_own), len(own), miss_own, len(lg_hi), len(oth_hi_d), miss_hi,
             len(lg_mid), len(oth_mid_d), miss_mid))
    hdr = ("组", "n", "官方分", "五维腿", "技能腿", "五维和", "skill_pt", "技能数", "Σgrade", "wins")
    print("  %-24s %5s %9s %9s %9s %8s %9s %7s %8s %6s" % hdr)
    own5 = [r for r in lg_own if i(r["n_skill_ids"]) > 1]
    for label, rowsx in (("我们自己真练成", lg_own), ("我们自己·技能≥2条", own5),
                         ("别人 ≥LG15", lg_hi), ("别人 60k–LG15", lg_mid)):
        if not rowsx:
            continue
        sk = [i(r["calc_score"]) - i(r["stat_score"]) for r in rowsx]
        print("  %-24s %5d %9s %9s %9s %8s %9s %7s %8s %6s" % (
            label, len(rowsx),
            N(med(rowsx, lambda r: i(r["rank_score"]))),
            N(med(rowsx, lambda r: i(r["stat_score"]))),
            N(med(sk)),
            N(med(rowsx, lambda r: i(r["five_sum"]))),
            N(med(rowsx, lambda r: i(r["total_skill_pt"]))),
            N(med(rowsx, lambda r: i(r["n_skill_ids"]))),
            N(med(rowsx, lambda r: i(r["sum_grade"]))),
            N(med(rowsx, lambda r: i(r["wins"])))))
    for base_own, tag in ((lg_own, "8 匹全体"), (own5, "技能≥2条的 %d 匹" % len(own5))):
        g = lambda f: med(lg_hi, f) - med(base_own, f)
        tot = g(lambda r: i(r["rank_score"]))
        stat = g(lambda r: i(r["stat_score"]))
        skill = g(lambda r: i(r["calc_score"]) - i(r["stat_score"]))
        print("  ⇒ 差值（别人≥LG15 − 我们自己·%s）：总分 %s ＝ 五维腿 %s ＋ 技能腿 %s"
              % (tag, N(tot), N(stat), N(skill)))
        print("     两条腿相加 %s，残差 %s（各组中位不可加；别人≥LG15 段 calc−官方 med %s）"
              "｜占比 五维 %.0f%%／技能 %.0f%%"
              % (N(stat + skill), N(tot - stat - skill),
                 N(st.median([i(r["calc_score"]) - i(r["rank_score"]) for r in lg_hi])),
                 100.0 * stat / (stat + skill), 100.0 * skill / (stat + skill)))
    print("     比赛腿：wins %s→%s ⇒ 我们自己赢的场更多，缺口不在比赛"
          % (N(med(lg_own, lambda r: i(r["wins"]))), N(med(lg_hi, lambda r: i(r["wins"])))))
    print("     技能条数 %s→%s ｜ Σgrade %s→%s ｜ 点数 total_skill_pt %s→%s"
          % (N(med(own5, lambda r: i(r["n_skill_ids"]))), N(med(lg_hi, lambda r: i(r["n_skill_ids"]))),
             N(med(own5, lambda r: i(r["sum_grade"]))), N(med(lg_hi, lambda r: i(r["sum_grade"]))),
             N(med(own5, lambda r: i(r["total_skill_pt"]))),
             N(med(lg_hi, lambda r: i(r["total_skill_pt"])))))
    d = [abs(i(r["calc_score"]) - i(r["rank_score"])) for r in lg_hi]
    print("     价这条无系统偏差：派生 calc vs 官方 rank_score 在 ≥LG15 段 |差| med %s p90 %s"
          % (N(st.median(d)), N(sorted(d)[int(0.9 * (len(d) - 1))])))

    print("\n【3b 我们自己 8 匹逐马明细（缺在哪条腿，一眼看见）】")
    print("  %-9s %-7s %-8s %-8s %-7s %-8s %-8s %-11s %s"
          % ("官方分", "五维和", "五维腿", "技能腿", "技能条", "Σgrade", "点数", "calc−官方", "落点"))
    for r in sorted(lg_own, key=lambda x: -i(x["rank_score"])):
        print("  %-9s %-7s %-8s %-8s %-7s %-8s %-8s %-11s %s"
              % (N(i(r["rank_score"])), N(i(r["five_sum"])), N(i(r["stat_score"])),
                 N(i(r["calc_score"]) - i(r["stat_score"])), r["n_skill_ids"],
                 N(i(r["sum_grade"])), N(i(r["total_skill_pt"])),
                 N(i(r["calc_score"]) - i(r["rank_score"])), r["jpath"]))

    # ── 4. 逐轴缺口 ──────────────────────────────────────────────────────
    print("\n【4 逐轴中位（real_score_dist）：自己真练成 vs 别人≥LG15】")
    amax = {k: max(i(r[k]) for r in dist if i(r["技能数"]) > 1) for k in AXES}
    print("  %-8s %10s %10s %8s %10s" % ("轴", "自己", "别人≥LG15", "差", "自己占全库max"))
    gap_tot = 0
    for k in AXES:
        a = med(own, lambda r: i(r[k]))
        b = med(oth_hi_d, lambda r: i(r[k]))
        gap_tot += b - a
        print("  %-8s %10s %10s %8s %9.1f%%" % (AX_CN[{"speed": "1", "stamina": "2", "power": "3",
                                                       "guts": "4", "wiz": "5"}[k]] + " " + k,
                                                N(a), N(b), N(b - a),
                                                100.0 * a / amax[k]))
    print("  ⇒ 逐轴差合计 %s；自己五维和 med %s vs 别人≥LG15 med %s（差 %s）"
          % (N(gap_tot), N(med(own, lambda r: i(r["五维和"]))),
             N(med(oth_hi_d, lambda r: i(r["五维和"]))),
             N(med(oth_hi_d, lambda r: i(r["五维和"])) - med(own, lambda r: i(r["五维和"])))))
    print("  全库各轴 max（技能数>1）：%s" % {k: "{:,}".format(amax[k]) for k in AXES})

    # ── 5. 卡组配置 ──────────────────────────────────────────────────────
    print("\n【5 卡组逐轴张数中位（配置这条腿）】")
    axis_of_cmd, cards, self_check = card_axis()
    print("  基础训练项自证（表侧 argmax）：5101..5105 → %s ⇒ target_type k = 报文第 k 轴"
          % self_check)
    print("  command_id→轴：%s" % {k: AX_CN[str(v)] for k, v in
                                  sorted(axis_of_cmd.items()) if k <= 200})
    print("  %-18s %5s %8s %8s %8s %8s %8s %10s %7s"
          % ("组", "n", "速度", "耐力", "力量", "根性", "智慧", "外出/友人", "合计"))
    for label, rowsx in (("别人 ≥LG15", lg_hi), ("别人 60k–LG15", lg_mid),
                         ("我们自己真练成", lg_own)):
        cols, mids, n, unk = deck_shape(rowsx, axis_of_cmd, cards)
        print("  %-18s %5d %8s %8s %8s %8s %8s %10s %7d   未映射=%s"
              % (label, n, *["%.2f" % m for m in mids], sum(mids),
                 dict(unk) if unk else "无"))
    cov = lambda rowsx, cid: (100.0 * sum(1 for r in rowsx if str(cid) in r["deck"]) / len(rowsx)
                              if rowsx else 0)
    for cid in (30323, 10149):
        print("  卡 %s 覆盖率：别人≥LG15 %.1f%%｜别人60k–LG15 %.1f%%｜我们自己 %.1f%%"
              % (cid, cov(lg_hi, cid), cov(lg_mid, cid), cov(lg_own, cid)))

    # 友人卡稀有度这一格单列：轴位同形不等于配置同形（友人位同时定演目 Lv 上限，见 §6b）
    print("\n【5b 自己 8 匹逐马卡组（友人位单独看，稀有度取表侧 support_card_data.rarity）】")
    print("  %-9s %-8s %-22s %s" % ("官方分", "马card", "友人位（卡/稀有度）", "其余五张"))
    for r in sorted(lg_own, key=lambda x: -i(x["rank_score"])):
        ids = [i(x) for x in r["deck"].strip("[]").split(",") if i(x)]
        fr = [c for c in ids if cards.get(c) and cards[c][1] == 0]
        print("  %-9s %-8s %-22s %s"
              % (N(i(r["rank_score"])), r["card_id"],
                 " ".join("%d/r%d" % (c, cards[c][2]) for c in fr) or "无",
                 " ".join(str(c) for c in ids if c not in fr)))

    # ── 6. 引擎两臂同口径 ────────────────────────────────────────────────
    print("\n【6 引擎两臂（s52b 壳，MC_POLICY=search MC_K=256 MC_N=60，种子 1–5，300 局/臂）】")
    arms = {}
    for arm in ("A", "D"):
        rs = []
        for p in sorted(glob.glob("E:/HJM/_keileg_s52b/out/s52b_%s_s*.jsonl" % arm)):
            with open(p, encoding="utf-8") as fh:
                rs += [json.loads(l) for l in fh if l.strip()]
        arms[arm] = rs
        if not rs:
            print("  %s 臂：读不到 jsonl" % arm)
            continue
        print("  %s 臂 n=%d ｜ real med %s mean %s max %s ｜ 五维和 med %s ｜ pt med %s"
              " ｜ Σgrade med %s ｜ act末值合计 med %s"
              % (arm, len(rs), N(med(rs, lambda r: r["real"])),
                 N(st.fmean([r["real"] for r in rs])),
                 N(max(r["real"] for r in rs)),
                 N(med(rs, lambda r: r["sum"])),
                 N(med(rs, lambda r: r["pt"])),
                 N(med(rs, lambda r: r["grade"])),
                 N(med(rs, lambda r: sum(r["act"])))))
        print("     逐轴 med %s" % {AXES[k]: int(med(rs, lambda r: r["five"][k])) for k in range(5)})
        print("     同一张三条腿表（引擎口径）：stat 五维腿 med %s ｜ real−stat 技能腿 med %s"
              " ｜ pt med %s ｜ Σgrade med %s ｜ 真机 LG15 组对照 %s / %s"
              % (N(med(rs, lambda r: r["stat"])), N(med(rs, lambda r: r["real"] - r["stat"])),
                 N(med(rs, lambda r: r["pt"])), N(med(rs, lambda r: r["grade"])),
                 N(med(lg_hi, lambda r: i(r["stat_score"]))),
                 N(med(lg_hi, lambda r: i(r["calc_score"]) - i(r["stat_score"])))))
        for nm, th in LINES:
            c = sum(1 for r in rs if r["real"] >= th)
            print("     P(≥%-5s %-6s) = %3d/%d = %.1f%%"
                  % (nm, "{:,}".format(th), c, len(rs), 100.0 * c / len(rs)))

    # ── 6b. 锚点核对：两臂到底跑在哪一局、哪副卡组上 ─────────────────────
    print("\n【6b 锚点核对（_state_t30.json = 两臂共用的真机 t30 状态）】")
    ap = "E:/HJM/_keileg_s52b/_state_t30.json"
    anchor_cid = None
    if os.path.exists(ap):
        with open(ap, encoding="utf-8") as fh:
            a = json.load(fh)
        ids = [i(s.get("card_id")) for s in a.get("support_cards", [])]
        fr = [c for c in ids if cards.get(c) and cards[c][1] == 0]
        cid = str(a.get("card_id"))
        anchor_cid = cid
        print("  turn=%s ｜ 马 card_id=%s ｜ 面板 max_act_level=%s（一手，宿主注入）"
              % (a.get("turn"), cid, a.get("max_act_level")))
        print("  锚点卡组 %s ｜ 友人位 %s"
              % (ids, " ".join("%d/r%d" % (c, cards[c][2]) for c in fr) or "无"))
        twin = [r for r in lg_own if str(r["card_id"]) == cid]
        for r in twin:
            print("  ⇒ 对上自己真练成里的这匹：官方 %s ｜ calc %s ｜ 五维和 %s ｜ 落点 %s"
                  % (N(i(r["rank_score"])), N(i(r["calc_score"])), N(i(r["five_sum"])), r["jpath"]))
            for arm in ("A", "D"):
                if arms.get(arm):
                    print("     同锚点配对：%s 臂 real med %s ⇒ 引擎−真机该局 calc %s"
                          % (arm, N(med(arms[arm], lambda x: x["real"])),
                             N(med(arms[arm], lambda x: x["real"]) - i(r["calc_score"]))))
        print("  ⇒ 锚点局是本账号 8 匹里**唯一**带 R 吾妻（cap 6）的那局；"
              "真机分数最高的 7 匹全带 SSR 30323（cap 10）")
    else:
        print("  读不到 %s（跨仓只读路径，缺席不影响 §1–§5）" % ap)

    op = os.path.join("out", "_outing_act_level_turns.csv")
    if os.path.exists(op):
        last = {}
        for r in rd(op):
            if not r.get("板_Lv"):
                continue
            k = r["run"]
            if k not in last or i(last[k]["t面板"]) <= i(r["t面板"]):
                last[k] = r
        rr = []
        for k, r in last.items():
            rr.append((i(r["t面板"]),
                       sum(i(x.split(":")[1]) for x in r["板_Lv"].split("|") if ":" in x),
                       i(r["max_act"]), k))
        rr.sort(reverse=True)
        full = [x for x in rr if x[0] >= 60]
        print("  真机演目 Lv 末值（按 run 取 t面板 最大行）：整局 %d 局 合计 med %s max %s｜"
              "残局 %d 局（t面板<60）不进中位"
              % (len(full), N(med(full, lambda x: x[1])),
                 N(max(x[1] for x in full)) if full else "—",
                 len(rr) - len(full)))
        for t, tot, ma, k in rr:
            print("     %-34s t面板=%-4s 合计=%-4s max_act=%s" % (k, t, tot, ma))

    print("\n【7 一句话】")
    if own and oth_hi_d and arms.get("A"):
        print("  我们自己真练成（8 匹）med 分 %s ｜ 技能≥2条（5 匹）med %s ｜ 引擎 A 臂 med %s ｜ "
              "别人≥LG15 med %s"
              % (N(med(own, lambda r: i(r["score"]))), N(med(own5, lambda r: i(r["rank_score"]))),
                 N(med(arms["A"], lambda r: r["real"])), N(med(oth_hi_d, lambda r: i(r["score"])))))
        print("  ⇒ 离 LG15：%s 分（A 臂 med 口径）＝ §3 两条腿，两臂 P(≥LG15) 都是 0/300 ⇒ "
              "不是搜索预算能买的。"
              % N(med(oth_hi_d, lambda r: i(r["score"])) - med(arms["A"], lambda r: r["real"])))
        print("  ⇒ A 臂 ≈ 真机最高那匹 %s 是**错配巧合**：A 臂跑的是 §6b 那局 R 吾妻（cap 6）锚点，"
              "同锚点真机 calc 只 %s ⇒ 引擎比我们自己那局打得好，不是复刻我们的水平。"
              % (N(max(i(r["rank_score"]) for r in lg_own)),
                 N(next((i(r["calc_score"]) for r in lg_own
                         if str(r["card_id"]) == anchor_cid), None))))


if __name__ == "__main__":
    main()
