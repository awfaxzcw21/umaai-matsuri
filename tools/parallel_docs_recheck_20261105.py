"""主线独立复算 #64（`docs/合宿干劲与体力阈值_20261105.md`）与 #65
（`docs/运气档报文一手_20261105.md`）的承重数字。

    python tools/parallel_docs_recheck_20261105.py        # A/B 两段：读派生 CSV + master.mdb，秒级
    python tools/parallel_docs_recheck_20261105.py p      # 再加报文段（全扫 10,583 包，约 4 分钟）

三段都不复用被测脚本的判据实现：
- `#64-A` 从 `out/_camp_mot_*.csv` 重数 29 条标记包 / 10 条可证伪 / §3 坐标云 / §5 效果号到达；
- `#64-B` 直接从 `master.mdb` 查 `target_type=20` 的归属与 `single_mode_chara_effect.effect_type`；
- `#65-A` 从 `out/_luck_census_20261105.csv` 重数逐局列、分层表、発表会分布，并**独立重算**
  Spearman ρ 与穷举置换 p（不用 `luck_census.py` 的统计函数）；
- `#65-P` 自己解报文，验三条「报文里没有第三档」的字段级根据。

只读，不改 tracked 文件、不写 `data/`。
"""

import collections
import csv
import itertools
import json
import math
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")

MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
fails = []


def chk(name, got, want):
    ok = got == want
    print("%-4s %-56s got=%-26s want=%s" % ("OK" if ok else "FAIL", name, got, want))
    if not ok:
        fails.append(name)


def show(name, val):
    print("     %-56s %s" % (name, val))


def rd(path):
    with open(path, encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def I(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ #64 A
def camp_samples():
    print("\n===== #64-A 29 条标记包与 §3 坐标 =====")
    s = rd("out/_camp_mot_samples.csv")
    chk("#64 标记包条数", len(s), 29)
    chk("#64 全部是 check_event（⇒ 0 条带 command_id）",
        sorted({x["req"] for x in s}), ["check_event"])
    chk("#64 落点面板回合", sorted({I(x["turn"]) for x in s}), [13, 37, 61])
    chk("#64 年目分布 9/10/10", sorted(collections.Counter(I(x["yr"]) for x in s).items()),
        [(1, 9), (2, 10), (3, 10)])
    chk("#64 带合宿事件号(215016/17/18)的包",
        len([x for x in s if x["event_id"] in ("215016", "215017", "215018")]), 28)
    fal = [x for x in s if x["falsifiable"] == "1"]
    chk("#64 可证伪（mot_before<5）", len(fal), 10)
    chk("#64 可证伪里 +1 / +0",
        sorted(collections.Counter(I(x["d_mot"]) for x in fal).items()), [(0, 1), (1, 9)])
    chk("#64 全 29 条 d_mot 从未超过 +1", max(I(x["d_mot"]) for x in s), 1)
    chk("#64 顶满(mot_before=5)而判不了的包", len(s) - len(fal), 19)
    chk("#64 标记包本身效果号无变化（新增/移除 0/29）",
        len([x for x in s if x["effects_prev"] != x["effects_after"]]), 0)

    def eff(x):
        return {int(v) for v in x.split(",") if v.strip().isdigit()}
    one = [x for x in fal if I(x["d_mot"]) == 1]
    zero = [x for x in fal if I(x["d_mot"]) == 0]
    chk("#64 0 样本恰好 1 条 = run203 面板61",
        [(x["chara"], I(x["turn"])) for x in zero], [("203", 61)])
    z = zero[0]
    show("#64 0 样本读数（v/mot/max/d_v/ratio/effects）",
         "%s→%s / %s→%s / max %s / d_v %s / %.4f→%.4f / %s / ev=%s" %
         (z["v_before"], z["v_after"], z["mot_before"], z["mot_after"], z["max_vital"],
          z["d_v"], float(z["ratio_before"]), float(z["ratio_after"]),
          z["effects_after"], z["event_id"]))
    chk("#64 0 样本效果号含 5（文档 §5 独占线索）", 5 in eff(z["effects_after"]), True)
    rng = lambda xs: (min(xs), max(xs))
    show("#64 +1 组的 v_before 云团", rng([int(x["v_before"]) for x in one]))
    show("#64 +1 组的 v_after 云团", rng([int(x["v_after"]) for x in one]))
    show("#64 +1 组的 ratio_before 云团",
         tuple(round(v, 4) for v in rng([float(x["ratio_before"]) for x in one])))
    show("#64 +1 组的 ratio_after 云团",
         tuple(round(v, 4) for v in rng([float(x["ratio_after"]) for x in one])))
    show("#64 +1 组的 d_v 取值", sorted({I(x["d_v"]) for x in one}))
    show("#64 +1 组的 mot_before 云团", rng([int(x["mot_before"]) for x in one]))
    show("#64 +1 组的 max_vital 取值", sorted({I(x["max_vital"]) for x in one}))
    chk("#64 0 样本落在 +1 云团内部（v_before 42 ∈ [21,79]）",
        min(int(x["v_before"]) for x in one) < int(z["v_before"]) < max(int(x["v_before"]) for x in one),
        True)
    for col, want in [("d_v", "15"), ("mot_before", "3"), ("max_vital", "108")]:
        chk("#64 0 样本的 %s 与若干 +1 样本同值（阈值选不了边）" % col,
            len([x for x in one if x[col] == want]) > 0, True)
    # 两条定向反证
    def pick(run, turn):
        return [x for x in s if x["chara"] == run and I(x["turn"]) == turn]
    a = pick("203", 37)
    b = pick("203", 61)
    cc = pick("201", 37)
    chk("#64 反证 1：@203 t37 体力 30→45、干劲 4→5（给了）",
        [(x["v_before"], x["v_after"], x["mot_before"], x["mot_after"]) for x in a],
        [("30", "45", "4", "5")])
    chk("#64 反证 1：@203 t61 体力更高(42→57)、max 同、d_v 同＝15（没给）",
        [(x["v_before"], x["v_after"], x["max_vital"], x["d_v"], x["d_mot"]) for x in b],
        [("42", "57", "108", "15", "0")])
    chk("#64 反证 2：@201 t37 体力 68→83（更高）却给了 +1",
        [(x["v_before"], x["v_after"], x["mot_before"], x["d_mot"]) for x in cc],
        [("68", "83", "3", "1")])


def camp_cross():
    print("\n===== #64-A2 全库基准率（356 条体力净增包） =====")
    x = rd("out/_camp_mot_cross_rows.csv")
    chk("#64 体力净增包数", len(x), 356)
    up = [r for r in x if I(r["d_mot"]) > 0]
    chk("#64 其中干劲也上升", len(up), 57)
    show("#64 基准率", "%.1f%%" % (100.0 * len(up) / len(x)))
    agg = collections.defaultdict(lambda: [0, 0])
    for r in x:
        agg[r["dv_bucket"]][0] += 1
        if I(r["d_mot"]) > 0:
            agg[r["dv_bucket"]][1] += 1
    want = {"0-9": (55, 12), "10-19": (183, 26), "20-49": (76, 11), "50+": (42, 8)}
    chk("#64 逐 Δv 档（档内总数, 干劲上升数）",
        {k: tuple(v) for k, v in sorted(agg.items())}, want)
    cr = rd("out/_camp_mot_cross.csv")
    chk("#64 与 cross.csv 的 dplus1+dplus2 一致",
        sum(I(r["dplus1"]) + I(r["dplus2"]) for r in cr), 57)
    # 干劲档位的 ratio 区间重叠
    band = collections.defaultdict(list)
    for r in x:
        mv = I(r["max_vital"])
        if mv:
            band[I(r["mot_after"])].append(int(r["v_after"]) / mv)
    show("#64 各 mot 档的 vital/max_vital 区间（看是否重叠）",
         {k: (round(min(v), 3), round(max(v), 3), len(v)) for k, v in sorted(band.items()) if v})


def camp_arrivals():
    print("\n===== #64-A3 效果号到达 67 次 × 表侧 effect_type =====")
    a = rd("out/_camp_mot_arrivals.csv")
    chk("#64 到达记录数", len(a), 67)
    c = sqlite3.connect("file:%s?mode=ro" % MDB.replace("\\", "/"), uri=True)
    et = dict(c.execute("select id, effect_type from single_mode_chara_effect"))
    c.close()
    grp = collections.defaultdict(lambda: collections.Counter())
    for r in a:
        grp[et.get(I(r["effect"]))][I(r["d_mot"]) > 0 and 1 or (I(r["d_mot"]) < 0 and -1 or 0)] += 1
    chk("#64 effect_type=1 到达次数", sum(grp[1].values()), 56)
    chk("#64 effect_type=1 的 Δmot（负/零/正）", (grp[1][-1], grp[1][0], grp[1][1]), (0, 55, 1))
    chk("#64 effect_type=2 到达次数", sum(grp[2].values()), 11)
    chk("#64 effect_type=2 的 Δmot（负/零/正）", (grp[2][-1], grp[2][0], grp[2][1]), (8, 3, 0))
    e5 = [r for r in a if I(r["effect"]) == 5]
    chk("#64 效果号 5 全库到达 1 次且当次 Δmot=0",
        [(I(r["d_mot"]), r["in_camp"]) for r in e5], [(0, "False")])
    show("#64 效果号 5 到达位置 (run, 该表 turn 轴)", [(r["run"].split("@")[1], r["turn"]) for r in e5])
    show("#64 效果号 5 表侧 type", et.get(5))
    # 「挂减益族的 4 条里有 3 条照样给 +1」
    s = rd("out/_camp_mot_samples.csv")
    fal = [x for x in s if x["falsifiable"] == "1"]

    def effset(row):
        return {int(v) for v in row["effects_after"].split(",") if v.strip().isdigit()}
    chk("#64 效果号 5 只在 0 样本那条出现（独占）",
        [x["chara"] + "/" + x["turn"] for x in fal if 5 in effset(x)], ["203/61"])
    deb = [x for x in fal if any(et.get(v) == 2 for v in effset(x))]
    chk("#64 可证伪且挂减益族的包", len(deb), 4)
    chk("#64 其中照样给 +1 的", len([x for x in deb if I(x["d_mot"]) == 1]), 3)
    show("#64 这 4 条 (run,turn,d_mot)",
         [(x["chara"], I(x["turn"]), I(x["d_mot"])) for x in deb])


# ------------------------------------------------------------------ #64 B
def camp_tables():
    print("\n===== #64-B master.mdb：干劲 target_type 的家 =====")
    c = sqlite3.connect("file:%s?mode=ro" % MDB.replace("\\", "/"), uri=True)
    cols = [d[1] for d in c.execute("pragma table_info(single_mode_training_effect)")]
    scen = "scenario_id" in cols
    where = "where scenario_id=15" if scen else ""
    w = where or "where 1=1"
    hist = collections.Counter(r[0] for r in
                               c.execute("select target_type from single_mode_training_effect %s" % where))
    show("#64 target_type 直方图（scenario_id=15）", dict(sorted(hist.items())))
    chk("#64 target_type=20 的行数", hist.get(20, 0), 6)
    rows = [(r[0], r[1], r[2]) for r in c.execute(
        "select command_id, sub_id, effect_value from single_mode_training_effect "
        "%s and target_type=20 order by command_id, sub_id" % w)]
    show("#64 发干劲的 6 行 (cmd, sub, value)", rows)
    chk("#64 发干劲的 command 只有外出 301/302/303/304",
        sorted({r[0] for r in rows}), [301, 302, 303, 304])
    train = [101, 102, 103, 105, 106, 5101, 5102, 5103, 5104, 5105]
    ph = ", ".join("?" * len(train))
    have = {r[0] for r in c.execute(
        "select distinct command_id from single_mode_training_effect %s and command_id in (%s)"
        % (w, ph), train)}
    show("#64 训练/稽古 command 在这张表里有行的", sorted(have))
    chk("#64 训练/稽古 command 一条 target_type=20 都没有",
        len([r for r in c.execute("select command_id from single_mode_training_effect "
                                  "%s and command_id in (%s) and target_type=20"
                                  % (w, ph), train)]), 0)
    chk("#64 cmd 104 在 scen15 这张表里没有行", 104 in {
        r[0] for r in c.execute("select distinct command_id from single_mode_training_effect %s" % where)}, False)
    # 表侧是否存在「体力→干劲档位表」
    tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
    show("#64 master.mdb 表数", len(tabs))
    hit = []
    for t in tabs:
        cols_t = [d[1] for d in c.execute("pragma table_info(%s)" % t)]
        if any("motivat" in x.lower() for x in cols_t):
            hit.append(t)
    chk("#64 带 motivat* 列的表（都不是玩家体力→干劲档位）", sorted(hit),
        sorted(["race_motivation_rate", "single_mode_npc", "single_mode_difficulty_data",
                "main_story_race_chara_data"]))
    idx = [r for r in c.execute("select name from sqlite_master where type='index'")]
    by_story = [t for t in tabs if any(d[1] == "story_id" for d in
                                       c.execute("pragma table_info(%s)" % t))]
    show("#64 带 story_id 列的表（看事件加项有没有家）", by_story)
    chk("#64 没有按 story_id 索引效果值的表",
        [t for t in by_story if "effect" in t and t != "single_mode_chara_effect"], [])
    c.close()


# ------------------------------------------------------------------ #65 A
def rank(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    r = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for k in order[i:j + 1]:
            r[k] = (i + j) / 2.0 + 1
        i = j + 1
    return r


def spearman(x, y):
    rx, ry = rank(x), rank(y)
    mx = sum(rx) / len(rx)
    my = sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")


def perm_p(x, y, exact_limit=8):
    n = len(x)
    obs = abs(spearman(x, y))
    if n <= exact_limit:
        perms = list(itertools.permutations(range(n)))
    else:
        import random
        rng = random.Random(20261105)
        perms = [list(rng.sample(range(n), n)) for _ in range(20000)]
    ge = sum(1 for p in perms if abs(spearman(x, [y[i] for i in p])) >= obs - 1e-12)
    return (ge + 1) / len(perms)


def luck_census():
    print("\n===== #65-A 逐局表重聚 + 独立重算相关 =====")
    rows = rd("out/_luck_census_20261105.csv")
    chk("#65 逐局行数（13 局）", len(rows), 13)
    runs = [I(r["chara"]) for r in rows]
    show("#65 chara_id 清单", runs)
    chk("#65 Σ配对训练手", sum(I(r["n_hand"]) for r in rows), 353)
    chk("#65 Σ成功手 n_ok", sum(I(r["n_ok"]) for r in rows), 347)
    chk("#65 Σ失败手 n_fail（result_state=1）", sum(I(r["n_fail"]) for r in rows), 6)
    chk("#65 Σ其它 state", sum(I(r["n_other_state"]) for r in rows), 0)
    chk("#65 每局 state 取值只出现 1/2",
        sorted({v for r in rows for v in json.loads(r["state_values"])}), [1, 2])
    # Q3 分层：把逐局的 fail_by_seg 加起来
    seg = collections.defaultdict(lambda: [0, 0.0, 0])
    for r in rows:
        for k, v in json.loads(r["fail_by_seg"]).items():
            seg[k][0] += v[0]
            seg[k][1] += v[1]
            seg[k][2] += v[2]
    show("#65 Q3 分层（全部局）失败/期望/手",
         {k: (v[0], round(v[1], 2), v[2]) for k, v in sorted(seg.items())})
    chk("#65 Q3 手合计==353", sum(v[2] for v in seg.values()), 353)
    chk("#65 Q3 失败合计==6", sum(v[0] for v in seg.values()), 6)
    chk("#65 Q3 前期 65 手", seg["前期"][2], 65)
    chk("#65 Q3 一段合宿后 108 手", seg["一段合宿后"][2], 108)
    chk("#65 Q3 二段合宿后 104 手", seg["二段合宿后"][2], 104)
    chk("#65 Q3 末段 76 手", seg["末段"][2], 76)
    # Q4 発表会
    chk("#65 Σ発表会场次", sum(I(r["n_cp"]) for r in rows), 29)
    chk("#65 Σ大成功（result_type=2）", sum(I(r["n_great"]) for r in rows), 16)
    chk("#65 Σ成功（result_type=1）", sum(I(r["n_success_cp"]) for r in rows), 13)
    show("#65 大成功率", "%.1f%%" % (100.0 * 16 / 29))
    chk("#65 check_point 数组最长 3 条", max(I(r["cp_len_max"]) for r in rows), 3)
    show("#65 201–204 的 cp_detail（大成功 0）",
         [(I(r["chara"]), r["cp_detail"]) for r in rows if I(r["chara"]) in (201, 202, 203, 204)])
    # Q6 比赛腿
    chk("#65 跨局可比场次合计", sum(I(r["race_dev_n"]) for r in rows), 156)
    show("#65 L3 逐局（文档 §4 列）", [I(r["race_dev_sum"]) for r in rows])
    chk("#65 L3 合计", sum(I(r["race_dev_sum"]) for r in rows), -7 + 1 + 1 + 1 + 0 + 0 + 0 - 5 + 0 + 0 + 0 + 0)
    # 事件腿
    full = [r for r in rows if I(r["tp_max"]) == 78]
    chk("#65 整局（面板到 78）9 局", len(full), 9)
    show("#65 整局清单", [I(r["chara"]) for r in full])
    chk("#65 Σ去重事件（认真局口径 9 局）", sum(I(r["n_ev"]) for r in full), 1101)
    show("#65 逐局 事件/随机项", [(I(r["chara"]), I(r["n_ev"]), I(r["n_ev_rand"])) for r in full])
    # 关联强度：独立重算（五维分用 score_end，不用 five_end 那串逐轴）
    chk("#65 五维分列对照文档 §4（Σ score_end）",
        [I(r["score_end"]) for r in rows],
        [41843, 46634, 50373, 49976, 33537, 886, 42201, 545, 1314,
         13730, 15069, 16002, 16496])
    ser = {r["chara"]: r for r in rows}
    serious = ["179", "168", "169", "170", "171"]
    farm = ["201", "202", "203", "204"]
    for tag, ids in [("跨配置 9 局", [str(x) for x in sorted(ser, key=int)
                                      if I(ser[x]["tp_max"]) == 78 and ser[x]["rank_score"]]),
                     ("认真局 5", serious), ("刷局 4", farm)]:
        g = [ser[k] for k in ids]
        g = [r for r in g if r["rank_score"] not in ("", "None")]
        xs_l1 = [float(r["L1"]) for r in g]
        ys = [float(r["rank_score"]) for r in g]
        yfive = [float(r["score_end"]) for r in g]
        l2 = [float(r["n_great"]) for r in g]
        l3 = [float(r["race_dev_sum"]) for r in g]
        out = {}
        for nm, (xs, yy) in {"L1~官方分": (xs_l1, ys), "L1~五维分": (xs_l1, yfive),
                             "L2~五维分": (l2, yfive), "L2~官方分": (l2, ys),
                             "L3~五维分": (l3, yfive)}.items():
            if len(set(xs)) < 2 or len(set(yy)) < 2:
                out[nm] = "无方差，判不了"
            else:
                rho = spearman(xs, yy)
                out[nm] = "%+.3f p=%.4f n=%d" % (rho, perm_p(xs, yy), len(xs))
        show("#65 %s 独立重算" % tag, out)
    # 合成 Z（等权，L1 用 CSV 原符号）
    def zs(vals):
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / (len(vals) - 1))
        return [(v - mu) / sd for v in vals]
    ids9 = [str(x) for x in sorted(ser, key=int)
            if I(ser[x]["tp_max"]) == 78 and ser[x]["rank_score"]]
    g9 = [ser[k] for k in ids9]
    zc = [a + b + c for a, b, c in zip(zs([float(r["L1"]) for r in g9]),
                                       zs([float(r["n_great"]) for r in g9]),
                                       zs([float(r["race_dev_sum"]) for r in g9]))]
    y9 = [float(r["rank_score"]) for r in g9]
    show("#65 合成Z~官方分（L1 原符号）", "%+.3f p=%.4f n=%d" % (
        spearman(zc, y9), perm_p(zc, y9), len(zc)))
    zc2 = [-a + b + c for a, b, c in zip(zs([float(r["L1"]) for r in g9]),
                                         zs([float(r["n_great"]) for r in g9]),
                                         zs([float(r["race_dev_sum"]) for r in g9]))]
    show("#65 合成Z~官方分（仅 L1 取反=「期望−失败」）", "%+.3f p=%.4f n=%d" % (
        spearman(zc2, y9), perm_p(zc2, y9), len(zc2)))


# ------------------------------------------------------------------ #65 P
def camp_rows():
    print("\n===== #64-C 逐条重解 29 对报文（不碰 #53/#64 的取数代码） =====")
    import msgpack_lite as ml

    def fields(name):
        d = ml.decode_file(RAW + name)
        data = d.get("data") or {}
        ci = data.get("chara_info") or {}
        fi = ((data.get("festa_data_set") or {}).get("festa_info") or {})
        return {"vital": ci.get("vital"), "max_vital": ci.get("max_vital"),
                "mot": ci.get("motivation"), "camp_left": fi.get("summer_camp_remain_turn_num"),
                "turn": ci.get("turn"), "chara": ci.get("single_mode_chara_id"),
                "eff": sorted(int(v) for v in (ci.get("chara_effect_id_array") or []))}

    s = rd("out/_camp_mot_samples.csv")
    bad, markers, camp_ev, fal = [], 0, [], []
    for x in s:
        a, b = fields(x["file_prev"]), fields(x["file"])
        if (b["camp_left"] == 4 and a["camp_left"] == 0):
            markers += 1
        d = (b["mot"] or 0) - (a["mot"] or 0)
        if (str(a["vital"]) != x["v_before"] or str(b["vital"]) != x["v_after"]
                or str(b["max_vital"]) != x["max_vital"] or str(a["mot"]) != x["mot_before"]
                or str(b["mot"]) != x["mot_after"] or d != I(x["d_mot"])
                or str(b["turn"]) != x["turn"] or str(b["chara"]) != x["chara"]):
            bad.append((x["chara"], x["turn"], a, b))
        if a["eff"] != sorted(int(v) for v in x["effects_prev"].split(",") if v.strip().isdigit()):
            bad.append(("eff_prev", x["chara"], x["turn"], a["eff"], x["effects_prev"]))
        if b["eff"] != sorted(int(v) for v in x["effects_after"].split(",") if v.strip().isdigit()):
            bad.append(("eff_after", x["chara"], x["turn"], b["eff"], x["effects_after"]))
        if x["falsifiable"] == "1":
            fal.append((x["chara"], I(x["turn"]), d))
    chk("#64-C 29 对报文逐字段重解无出入（含 effects）", bad, [])
    chk("#64-C 29 条全部是 camp_left 0→4 标记包", markers, 29)
    show("#64-C 可证伪 10 条重解后的 Δmot", sorted(collections.Counter(d for _, _, d in fal).items()))
    chk("#64-C 重解的 Δmot 分布 +1=9 / +0=1",
        sorted(collections.Counter(d for _, _, d in fal).items()), [(0, 1), (1, 9)])


def luck_packets():
    print("\n===== #65-P 报文段：大成功档到底读不读得到 =====")
    import msgpack_lite as ml
    keys = collections.Counter()
    state = collections.Counter()
    cp_len = 0
    cp_keys = collections.Counter()
    n_ci = n_noci = n_cr = n_bin = 0
    series = collections.defaultdict(list)
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".bin"):
            continue
        n_bin += 1
        try:
            d = ml.decode_file(RAW + f)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        data = d.get("data") if isinstance(d.get("data"), dict) else d
        ci = data.get("chara_info") if isinstance(data, dict) else None
        if isinstance(ci, dict):
            n_ci += 1
            if ci.get("turn") is not None:
                v = d.get("viewer_id") or (d.get("data_headers") or {}).get("viewer_id")
                fi = ((data.get("festa_data_set") or {}).get("festa_info") or {})
                series["%s@%s@%s" % (v, ci.get("single_mode_chara_id"),
                                     ci.get("start_time"))].append(
                    (ci.get("turn"), f, fi.get("summer_camp_remain_turn_num"),
                     ci.get("scenario_id")))
        elif isinstance(data, dict) and ("chara_info" not in data):
            n_noci += 1
        cr = data.get("command_result") if isinstance(data, dict) else None
        if isinstance(cr, dict):
            n_cr += 1
            keys.update(cr.keys())
            state[cr.get("result_state")] += 1
        fds = data.get("festa_data_set") if isinstance(data, dict) else None
        arr = (fds or {}).get("check_point_result_array")
        if isinstance(arr, list):
            cp_len = max(cp_len, len(arr))
            for e in arr:
                if isinstance(e, dict):
                    cp_keys.update(e.keys())
    show("#65 报文 .bin 数", n_bin)
    chk("#65 带 command_result 的报文", n_cr, 472)
    chk("#65 result_state 直方图（无第三档）", dict(state), {1: 6, 2: 466})
    chk("#65 command_result 的键只有三个", sorted(keys), ["command_id", "result_state", "sub_id"])
    chk("#65 check_point_result_array 全库最长", cp_len, 3)
    chk("#65 数组元素的键", sorted(cp_keys), ["result_type", "schedule_id"])
    # 顺带独立普查 #64 的标记包母体（同一次扫描，不复用 #53/#64 的取数）
    # 口径：同局（viewer@chara@start_time）按 (面板回合, 文件名) 排序的逐包序列里
    #       `summer_camp_remain_turn_num` 由 0→4 的那条包＝合宿正式开始（可发生在同一回合内部）
    show("#64-C 逐局序列：局数 / 响应包数",
         (len(series), sum(len(v) for v in series.values())))
    show("#64-C 本 viewer 的局数", len([r for r in series if r.startswith("238107205@")]))
    show("#64-C camp_left 取值分布",
         sorted(collections.Counter(c for rs in series.values() for _, _, c, _ in rs).items(),
                key=lambda kv: str(kv[0])))
    onsets = []
    for run, rs in series.items():
        if not run.startswith("238107205@"):
            continue
        rs.sort()
        for (_, _, c0, _), (t1, f1, c1, s1) in zip(rs, rs[1:]):
            if c0 == 0 and c1 == 4 and s1 == 15:
                onsets.append((run.split("@")[1], t1, f1))
    onsets.sort(key=lambda x: (x[0], x[1]))
    chk("#64-C 全库 camp_left 0→4 标记包（scen15／本 viewer）== 29", len(onsets), 29)
    chk("#64-C 落点回合只有 13/37/61", sorted({t for _, t, _ in onsets}), [13, 37, 61])
    show("#64-C 逐局标记包数", sorted(collections.Counter(r for r, _, _ in onsets).items()))
    chk("#64-C 走到合宿格的局数 == 10", len({r for r, _, _ in onsets}), 10)
    chk("#64-C 标记包文件名与样本表逐条相同",
        sorted(f for _, _, f in onsets),
        sorted(x["file"] for x in rd("out/_camp_mot_samples.csv")))
    show("#64-C 带 chara_info 的响应包", n_ci)


def main():
    os.chdir(os.path.join(HERE, ".."))
    only = sys.argv[1] if len(sys.argv) > 1 else "ab"
    if "a" in only:
        camp_samples()
        camp_cross()
        camp_arrivals()
    if "b" in only:
        camp_tables()
        luck_census()
    if "p" in only:
        luck_packets()
    if "c" in only:
        camp_rows()
    print("\n===== 结论 =====")
    if fails:
        print("!! %d 条复算不一致：%s" % (len(fails), fails))
        return 1
    print("全部复算一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
