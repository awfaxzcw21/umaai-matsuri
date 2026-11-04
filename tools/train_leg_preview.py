"""训练腿 PT「预告 vs 预告」对账的真机侧（#40）。

板上报文 `home_info.command_info_array[cid].params_inc_dec_info_array` 里的
`target_type=30` 就是**训练腿（普通训练那一条）的技能Pt 预告**——#34 已用 227/229 手
恒等式 `实到 = home[30] + festa[30]` 钉住它的语义。引擎同位置的可比量是
`MatsuriGame::training_leg(idx).pt`（`crates/matsuri-core/src/game/mod.rs:559`）。

这一支只收真机：把每一块板的每个训练位预告摊平成
`回合 × 训练位 × 等级 × 人头 × 教诲 → (Pt, 五维, 体力)`，
再按 (等级, 人头) 分桶出中位数与 **Pt/Σ五维** 比值。比值是关键判据：
引擎里 Pt 和五维**共用** `friend_mult` 与 `effect_mult` 两个乘区
（`pt = base_pt·pt_mult·friend_mult`，五维同乘 `friend_mult`），
所以比值能**把乘区约掉**——比值对不上才是 Pt 独有的乘区缺了，比值对得上就是基础表缺了。

    python tools/train_leg_preview.py            # 全量扫，出 CSV + 分桶表
"""

import collections
import csv
import glob
import os
import statistics
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
OUT = "out/train_leg_preview_20261103.csv"
AXES = ("speed", "stamina", "power", "guts", "wiz")
TT_PT, TT_VITAL = 30, 10
PLAIN = {101, 102, 103, 104, 105, 106}  # 板上报文 command_type=1 的训练位；哪个位练哪几维由下面的轴型表量，不写死
KEIKO = {5101: "速", 5102: "耐", 5103: "力", 5104: "根", 5105: "智"}


def decode_all():
    for f in sorted(os.listdir(RAW)):
        if not f.endswith(".bin"):
            continue
        try:
            d = ml.decode_file(RAW + f)
        except Exception:
            continue
        data = d.get("data")
        if isinstance(data, dict):
            yield f, data


def preview_of(entry):
    """params_inc_dec_info_array -> (Pt, [五维], 体力)。缺列记 None。"""
    pt = vit = None
    five = [None] * 5
    for e in entry.get("params_inc_dec_info_array") or []:
        tt, v = e.get("target_type"), e.get("value")
        if tt == TT_PT:
            pt = v
        elif tt == TT_VITAL:
            vit = v
        elif 1 <= tt <= 5:
            five[tt - 1] = v
    return pt, five, vit


def camp_of(data):
    """合宿标记：`festa_data_set.festa_info.summer_camp_remain_turn_num > 0`。

    为什么要它：真机的 510x 板**不等于**合宿板（演目槽满时非合宿也走稽古），
    拿 kind 当合宿口径去对引擎的 `is_camp_turn()` 分层会得出假密度差。
    """
    f = (data.get("festa_data_set") or {}).get("festa_info") or {}
    n = f.get("summer_camp_remain_turn_num")
    return 1 if isinstance(n, int) and n > 0 else 0


def heads_of(entry):
    """人头拆三类计数：<100 的是卡组位，>=100 的是剧本成员/其它。

    `nsub` = 卡组人头里同时出现在 `sub_command_partner_array` 的那些，即**追加参与**：
    同一张卡可以既算在自己站的命令上，又作为 sub 参与另一条命令（实测 1,303/2,372 块板
    Σ参与 > 去重参与），所以 `heads_deck` 减掉 `nsub` 才是「站位排布」口径、与引擎
    `base.distribution` 里每槽 `PersonType::Card` 计数同义；不减掉才是吃友情乘区的那份。
    """
    ps = entry.get("training_partner_array") or []
    deck = [p for p in ps if isinstance(p, int) and p < 100]
    big = [p for p in ps if isinstance(p, int) and p >= 100]
    tips = entry.get("tips_event_partner_array") or []
    subs = {p for p in (entry.get("sub_command_partner_array") or [])
            if isinstance(p, int) and p < 100}
    return len(deck), len(big), len(tips), sum(1 for p in deck if p in subs)


def scan():
    rows = []
    pid_shape = collections.Counter()  # 人头 id 取值分布，用来确认「>=100」到底是啥
    for f, data in decode_all():
        cia = (data.get("home_info") or {}).get("command_info_array")
        if not cia:
            continue
        ci = data.get("chara_info") or {}
        turn, rid = ci.get("turn"), ci.get("single_mode_chara_id")
        if not isinstance(turn, int):
            continue
        for c in cia:
            cid = c.get("command_id")
            if not (cid in PLAIN or cid in KEIKO):
                continue
            if not c.get("params_inc_dec_info_array"):
                continue
            pt, five, vit = preview_of(c)
            if pt is None:
                continue
            nd, nb, nt, nsub = heads_of(c)
            for p in (c.get("training_partner_array") or []):
                pid_shape[p] += 1
            rows.append({
                "file": f, "run": rid, "turn": turn, "cid": cid,
                "kind": "keiko" if cid in KEIKO else "plain",
                "camp": camp_of(data),
                "level": c.get("level"), "heads_deck": nd, "heads_own": nd - nsub,
                "heads_sub": nsub, "heads_other": nb, "heads_total": nd + nb,
                "tips": nt, "failure_rate": c.get("failure_rate"), "is_enable": c.get("is_enable"),
                "pt": pt, "speed": five[0], "stamina": five[1], "power": five[2],
                "guts": five[3], "wiz": five[4], "vital": vit,
                "sp_at_board": ci.get("skill_point"),
            })
    return rows, pid_shape


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def table(rows, key, label):
    buckets = collections.defaultdict(list)
    for r in rows:
        buckets[key(r)].append(r)
    print("\n== %s ==" % label)
    print("%-14s %6s %8s %8s %8s %9s %9s" % (label, "样本", "Pt中位", "Σ五维", "Pt/Σ五维", "体力中位", "失败率"))
    for k in sorted(buckets, key=lambda x: (x if not isinstance(x, tuple) else tuple(str(i) for i in x))):
        g = buckets[k]
        pts = [r["pt"] for r in g]
        fives = [sum(r[a] or 0 for a in AXES) for r in g]
        name = k if not isinstance(k, tuple) else "x".join(map(str, k))
        mf, mp = med(fives), med(pts)
        ratio = (mp / mf) if (mf and mp) else None
        print("%-14s %6d %8s %8s %9s %9s %9s" % (
            name, len(g),
            "%.1f" % mp if mp is not None else "-",
            "%.1f" % mf if mf is not None else "-",
            "%.3f" % ratio if ratio is not None else "-",
            "%.0f" % med([r["vital"] for r in g]) if med([r["vital"] for r in g]) is not None else "-",
            "%.3f" % med([r["failure_rate"] for r in g]) if med([r["failure_rate"] for r in g]) is not None else "-",
        ))


def chosen_hands():
    """**执行腿**配对：板上六个位的预告都在 response 里，但「练了哪一个」在
    `single_mode_exec_command_request_common` 里（`command_type=1`）。
    沿用 `keiko_curve.py` 的口径——请求配对**上一拍**带板预告的 chara_info，
    这样每手只取真正被执行的那条 home 腿，才能和引擎的「每手」对齐。
    """
    import pt_ledger_scan as PS

    last = None  # (run, start, turn, {cid: row})
    out = []
    for path in sorted(glob.glob(os.path.join(RAW, "*.bin")), key=PS.sort_key):
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_notify_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            if hit is None or last is None:
                continue
            common = d[hit] or {}
            if PS.CHANNELS[hit] != "cmd" or common.get("command_type") != 1:
                continue
            cid = common.get("command_id")
            row = last["prev"].get(cid)
            if row is None:
                continue
            # 同一块板只允许配对一次：连续两个请求没有新板时不重复计入
            del last["prev"][cid]
            out.append(dict(row, run=last["run"], start=last["start"], turn=last["turn"],
                            paired=1))
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        cia = (data.get("home_info") or {}).get("command_info_array")
        if not cia:
            continue
        prev = {}
        for c in cia:
            cid = c.get("command_id")
            if not (cid in PLAIN or cid in KEIKO) or not c.get("params_inc_dec_info_array"):
                continue
            pt, five, vit = preview_of(c)
            if pt is None:
                continue
            nd, nb, nt, nsub = heads_of(c)
            prev[cid] = {
                "file": name, "cid": cid,
                "kind": "keiko" if cid in KEIKO else "plain",
                "camp": camp_of(data),
                "level": c.get("level"), "heads_deck": nd, "heads_own": nd - nsub,
                "heads_sub": nsub, "heads_other": nb, "tips": nt,
                "pt": pt, "speed": five[0], "stamina": five[1], "power": five[2],
                "guts": five[3], "wiz": five[4], "vital": vit,
            }
        if prev:
            last = {"run": ci.get("single_mode_chara_id"), "start": ci.get("start_time"),
                    "turn": ci.get("turn"), "prev": prev}
    return out


def report_chosen(rows, dump="out/train_leg_chosen_20261103.csv"):
    """执行腿的每局合计（t30–78，和引擎 `_state_t30.json` 锚点的段口径一致）。"""
    runs = collections.defaultdict(lambda: [0, 0, 0, 0, 0])  # 手 / Pt / Σ五维 / 稽古手 / 稽古Pt
    for r in rows:
        if not (30 <= (r["turn"] or 0) <= 78):
            continue
        five = sum(r[a] or 0 for a in AXES)
        k = runs[(r["run"], r["start"])]
        k[0] += 1
        k[1] += r["pt"]
        k[2] += five
        if r["kind"] == "keiko":
            k[3] += 1
            k[4] += r["pt"]
    if not runs:
        print("!! 执行腿配对为空")
        return
    with open(dump, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) + ["five_sum"])
        w.writeheader()
        for r in rows:
            r = dict(r)
            r["five_sum"] = sum(r[a] or 0 for a in AXES)
            w.writerow(r)
    print("执行腿配对明细 %d 手 → %s" % (len(rows), dump))
    n = len(runs)
    print("\n== 执行腿（配对到 exec_command_request 的 home 腿预告，t30–78）==")
    print("%-16s %6s %6s %9s %9s %9s %8s" % ("run", "训练手", "稽古手", "home腿Pt", "home腿五维", "每手Pt", "每头?"))
    for key in sorted(runs, key=lambda k: -runs[k][1]):
        v = runs[key]
        heads = [r["heads_deck"] for r in rows if (r["run"], r["start"]) == key
                 and 30 <= (r["turn"] or 0) <= 78]
        print("%-16s %6d %6d %9.0f %9.0f %9.2f %8.2f" % (
            str(key), v[0], v[3], v[1], v[2], v[1] / max(v[0], 1),
            sum(heads) / max(len(heads), 1)))
    print("均值：训练手 %.2f/局（其中稽古 %.2f）" % (
        sum(v[0] for v in runs.values()) / n, sum(v[3] for v in runs.values()) / n))
    print("home 腿 Pt 合计 %.1f/局（普通 %.1f + 稽古 home %.1f）｜home 腿 Σ五维 %.1f/局" % (
        sum(v[1] for v in runs.values()) / n,
        sum(v[1] - v[4] for v in runs.values()) / n,
        sum(v[4] for v in runs.values()) / n,
        sum(v[2] for v in runs.values()) / n))
    print("每手均值：Pt %.2f｜Σ五维 %.2f｜比值 %.3f" % (
        sum(v[1] for v in runs.values()) / max(sum(v[0] for v in runs.values()), 1),
        sum(v[2] for v in runs.values()) / max(sum(v[0] for v in runs.values()), 1),
        sum(v[1] for v in runs.values()) / max(sum(v[2] for v in runs.values()), 1)))
    hd = collections.Counter(r["heads_deck"] for r in rows if 30 <= (r["turn"] or 0) <= 78)
    print("执行腿的人头(卡组)分布：%s" % dict(sorted(hd.items())))
    lv = collections.Counter(r["level"] for r in rows if 30 <= (r["turn"] or 0) <= 78)
    print("执行腿的训练 Lv 分布：%s" % dict(sorted(lv.items())))


def main():
    rows, pid_shape = scan()
    os.makedirs("out", exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print("板上报文训练位预告行数 = %d（写到 %s）" % (len(rows), OUT))
    print("run 数 = %d｜回合范围 %d–%d" % (
        len({r["run"] for r in rows}), min(r["turn"] for r in rows), max(r["turn"] for r in rows)))
    print("\n== 人头 id 取值分布（>=100 那批是什么，先看形状再定语义）==")
    for pid, n in pid_shape.most_common(24):
        print("  id=%-6s 出现 %d" % (pid, n))

    plain = [r for r in rows if r["kind"] == "plain"]
    keiko = [r for r in rows if r["kind"] == "keiko"]
    print("\n普通训练预告 %d 条｜稽古 home 腿预告 %d 条" % (len(plain), len(keiko)))

    table(plain, lambda r: r["level"], "训练Lv")
    table(plain, lambda r: (r["level"], r["heads_deck"]), "(Lv,卡组人头)")
    table(plain, lambda r: (r["level"], r["heads_total"]), "(Lv,总人头)")
    table(plain, lambda r: r["tips"], "教诲人数")
    table(keiko, lambda r: (r["level"], r["heads_deck"]), "稽古 home 腿 (Lv,卡组人头)")
    print("\n== 每个训练位点亮哪几维（中位数，用来认 101–106 到底是哪个位）==")
    by_cid = collections.defaultdict(list)
    for r in plain:
        by_cid[r["cid"]].append(r)
    print("%-6s %6s %8s %8s %8s %8s %8s %8s" % ("cid", "样本", "速", "耐", "力", "根", "智", "Pt"))
    for cid in sorted(by_cid):
        g = by_cid[cid]
        cells = [med([r[a] for r in g]) for a in AXES] + [med([r["pt"] for r in g])]
        print("%-6s %6d %s" % (cid, len(g), " ".join("%8s" % ("%.1f" % c if c is not None else "-") for c in cells)))

    # 比值随当前技能Pt / 五维水位怎么走——「乘区藏在哪儿」的第二刀
    print("\n== Pt/Σ五维 比值 vs 该轴当前值水位（普通训练）==")
    ch = chosen_hands()
    report_chosen(ch)

    seg = collections.defaultdict(list)
    for r in plain:
        s = sum(r[a] or 0 for a in AXES)
        if not s:
            continue
        seg[min(4, s // 400)].append(r["pt"] / s)
    for k in sorted(seg):
        print("  Σ五维档 %-3d n=%-5d 比值中位 %.3f" % (k, len(seg[k]), statistics.median(seg[k])))


if __name__ == "__main__":
    main()
