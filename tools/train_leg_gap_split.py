"""#40 的第二刀：把「引擎训练腿每手只有真机一半」拆成
**公式效应**（同格子两边值不同）与**落位/手数效应**（两边落在不同格子里），
再把落位效应切三层（总手数 / 人头分布 / ——以及人头分布内部的 站位 × 选位 × 追加参与）。

分桶键只用 **卡组人头数**：板上报文 `level` 对 5101-5105 是演目 Lv、对 101-106 是训练 Lv，
引擎记的是训练 Lv ⇒ Lv 不能当共同键，人头数可以（`training_partner_array` 里 `<100` 的
就是卡组位，引擎侧只数 `PersonType::Card`）。

    python tools/train_leg_gap_split.py

数据源：
  真机 = `out/train_leg_chosen_20261103.csv`（执行腿配对明细，含 `heads_own`/`heads_sub`/`camp`）
        `out/train_leg_preview_20261103.csv`（每块板每个命令条目的预告，盘面 offer 用）
  引擎 = `E:/HJM/_keileg/out_head4000/trainleg2_result.json`（`train_legs`，4,000 局）
        `E:/HJM/_keileg/out_head4000/trainboard_result.json`（`train_board`，同壳另一臂）
结论与口径见 `docs/训练腿预告对账_20261104.md`。
"""

import argparse
import collections
import csv
import json

AXES = ("speed", "stamina", "power", "guts", "wiz")
MIN_HANDS = 20  # 「完整局」门槛：段内训练手数低于它的是被截断的 run，不进合计


def real_side(path, min_hands=MIN_HANDS):
    rows = [r for r in csv.DictReader(open(path, encoding="utf-8-sig"))
            if 30 <= int(r["turn"]) <= 78]
    per_run = collections.Counter((r["run"], r["start"]) for r in rows)
    keep = {k for k, v in per_run.items() if v >= min_hands}
    n = max(len(keep), 1)
    cells = collections.defaultdict(lambda: {"h": 0, "pt": 0.0, "five": 0.0})
    dropped = len(per_run) - len(keep)
    for r in rows:
        if (r["run"], r["start"]) not in keep:
            continue
        c = cells[(r["kind"], int(r["heads_deck"]))]
        c["h"] += 1
        c["pt"] += int(r["pt"])
        c["five"] += sum(int(r[a] or 0) for a in AXES)
    out = {}
    for (kind, hd), c in cells.items():
        out[(kind, hd)] = {"hands_per_run": c["h"] / n, "pt": c["pt"] / c["h"],
                           "five": c["five"] / c["h"]}
    return out, n, dropped, keep


def engine_side(path):
    j = json.load(open(path, encoding="utf-8"))["train_legs"]
    acc = collections.defaultdict(lambda: {"h": 0.0, "pt": 0.0, "five": 0.0})
    for r in j["per_cell"]:
        kind = "plain" if r["slot"] == "plain" else "keiko"
        c = acc[(kind, r["heads"])]
        c["h"] += r["hands_per_run"]
        c["pt"] += r["hands_per_run"] * r["pt"]
        c["five"] += r["hands_per_run"] * r["five"]
    return {k: {"hands_per_run": v["h"], "pt": v["pt"] / v["h"], "five": v["five"] / v["h"]}
            for k, v in acc.items()}


def split(kind, real, eng, label, metric="pt"):
    keys = sorted({k[1] for k in real if k[0] == kind} | {k[1] for k in eng if k[0] == kind})
    print("\n== %s（按卡组人头分桶）==" % label)
    print("%-6s %8s %8s %8s %8s %8s %8s" % ("人头", "真手/局", "引手/局", "真值/手", "引值/手",
                                            "落位效应", "公式效应"))
    tot = {"n_r": 0.0, "n_e": 0.0, "R": 0.0, "E": 0.0, "arr": 0.0, "frm": 0.0}
    for h in keys:
        r, e = real.get((kind, h)), eng.get((kind, h))
        nr = r["hands_per_run"] if r else 0.0
        ne = e["hands_per_run"] if e else 0.0
        vr = r[metric] if r else (e[metric] if e else 0.0)   # 缺格时用对侧值，效应项自动归零
        ve = e[metric] if e else (r[metric] if r else 0.0)
        arr = (ne - nr) * vr
        frm = ne * (ve - vr)
        tot["n_r"] += nr; tot["n_e"] += ne
        tot["R"] += nr * vr; tot["E"] += ne * ve
        tot["arr"] += arr; tot["frm"] += frm
        print("%-6d %8.2f %8.2f %8.1f %8.1f %8.1f %8.1f" % (h, nr, ne, vr, ve, arr, frm))
    print("%-6s %8.2f %8.2f %8.1f %8.1f %8.1f %8.1f" % (
        "合计", tot["n_r"], tot["n_e"], tot["R"] / max(tot["n_r"], 1e-9),
        tot["E"] / max(tot["n_e"], 1e-9), tot["arr"], tot["frm"]))
    print("真机 home 腿 %.0f %s/局 vs 引擎 %.0f ⇒ 缺口 %.0f｜其中 落位/手数 %.0f + 公式 %.0f" % (
        tot["R"], UNITS[metric], tot["E"], tot["E"] - tot["R"], tot["arr"], tot["frm"]))
    return tot


def split3(kind, real, eng, metric):
    """把「落位/手数效应」再切一刀，因为它混了两个不同的责任人：
       总手数（每回合打几手训练 = 决策层）与人头分布（一手站几个卡组位 = 站位模型）。

        缺口 = (N_e − N_r)·Σp_r·v_r        总手数效应（按真机的桶配比和桶均值）
             + N_e·Σ(p_e − p_r)·v_r       分布效应（按真机桶均值）
             + N_e·Σ p_e·(v_e − v_r)      公式效应
      三项相加恒等于 E − R（推导见函数末尾的 check）。
    """
    keys = sorted({k[1] for k in real if k[0] == kind} | {k[1] for k in eng if k[0] == kind})
    nr = {h: (real.get((kind, h)) or {}).get("hands_per_run", 0.0) for h in keys}
    ne = {h: (eng.get((kind, h)) or {}).get("hands_per_run", 0.0) for h in keys}
    vr = {h: (real.get((kind, h)) or {}).get(metric) for h in keys}
    ve = {h: (eng.get((kind, h)) or {}).get(metric) for h in keys}
    # 真机没有样本的桶，用引擎自己的值兜底 ⇒ 该项只进「总手数」，不污染分布/公式两项
    for h in keys:
        if vr[h] is None:
            vr[h] = ve[h] or 0.0
    N_r, N_e = sum(nr.values()), sum(ne.values())
    v_bar_r = sum(nr[h] / N_r * vr[h] for h in keys) if N_r else 0.0
    hands = (N_e - N_r) * v_bar_r
    dist = sum((ne[h] / N_e - nr[h] / N_r) * vr[h] for h in keys) * N_e if (N_e and N_r) else 0.0
    form = sum(ne[h] * ((ve[h] if ve[h] is not None else vr[h]) - vr[h]) for h in keys)
    E = sum(ne[h] * (ve[h] if ve[h] is not None else vr[h]) for h in keys)
    R = sum(nr[h] * vr[h] for h in keys)
    check = hands + dist + form - (E - R)
    print("  %-10s 总手数 %+8.0f｜人头分布 %+8.0f｜公式 %+8.0f  (合计 %+8.0f，残差 %.1e)"
          % (UNITS[metric], hands, dist, form, E - R, check))
    return {"hands": hands, "dist": dist, "form": form, "E": E, "R": R,
            "N_e": N_e, "N_r": N_r}


UNITS = {"pt": "Pt", "five": "五维和"}


def real_board(chosen_path, preview_path, keep):
    """真机侧「盘面 offer / 玩家 take」，按 kind × 合宿 分层（窗口与 `real_side` 一致）。

    offer = 同一条报文同一回合里那块盘面上各槽位的卡组人头数（`home_info.command_info_array`）；
    take  = 配对成功的那一手实际站在几个卡组头上。
    分层的理由：真机 `home_info` 里的 510x 稽古板**不只出现在合宿**（演目槽点亮就走稽古），
    拿 kind 整体去比引擎的 `is_camp_turn()` 分层会得出假密度差（之前那句 1.81 vs 0.91 就是这么错的）。
    """
    prev = collections.defaultdict(list)
    for r in csv.DictReader(open(preview_path, encoding="utf-8-sig")):
        prev[(r["file"], r["turn"], r["kind"])].append(int(r["heads_deck"]))
    acc = collections.defaultdict(lambda: {"b": 0, "bh": 0, "n": 0, "h": 0, "own": 0, "mx": 0,
                                           "ge2o": 0, "ge2t": 0,
                                           "offer": collections.Counter(),
                                           "take": collections.Counter()})
    for r in csv.DictReader(open(chosen_path, encoding="utf-8-sig")):
        if not (30 <= int(r["turn"]) <= 78) or (r["run"], r["start"]) not in keep:
            continue
        k = (r["kind"], r["camp"])
        a = acc[k]
        a["n"] += 1
        hd = int(r["heads_deck"])
        a["h"] += hd
        a["own"] += int(r["heads_own"])
        a["take"][hd] += 1
        board = prev.get((r["file"], r["turn"], r["kind"]))
        if not board:
            continue
        a["b"] += 1
        a["bh"] += sum(board) / len(board)
        a["offer"][max(board)] += 1
        a["ge2o"] += 1 if max(board) >= 2 else 0
        a["ge2t"] += 1 if hd >= 2 else 0
        a["mx"] += 1 if hd == max(board) else 0
    return acc


def real_offer(preview_path, keep_runs):
    """真机盘面侧（不看落子）：每板**站位**人头总数、最满位站位人头、条目数。

    单位提醒：一块非合宿板会同时报 101-106 与 5101-5105（点亮位的 10x 条目人头=0），
    所以「每条目人头」与引擎 `mean_slot_heads`（每**位**人头）**不是同一个变量**，
    只有「每板放卡总数」和「最满位人头」两边可比。
    """
    b = collections.defaultdict(list)
    camp_of_board = {}
    for r in csv.DictReader(open(preview_path, encoding="utf-8-sig")):
        if not (30 <= int(r["turn"]) <= 78) or r["run"] not in keep_runs:
            continue
        key = (r["file"], r["turn"])
        b[key].append(int(r["heads_own"]))
        camp_of_board[key] = r["camp"]
    st = collections.defaultdict(lambda: [0, 0, 0, 0])   # boards, max_own, sum_own, entries
    for key, own in b.items():
        s = st[camp_of_board[key]]
        s[0] += 1
        s[1] += max(own)
        s[2] += sum(own)
        s[3] += len(own)
    return st


def run_board(real_chosen, real_preview, eng_board, keep, n_runs):
    """盘面 offer / take 对照（`split3` 的「人头分布」项归责用）。

    两边可比的只有三个口径：**每板放卡总数**、**最满位站位人头**、**落子那人头**。
    「每条目人头」不可比：真机一块非合宿板报 ~7.3 条命令（点亮位的 10x 条目人头=0），
    引擎只有 5 个位。
    """
    import math
    real = real_board(real_chosen, real_preview, keep)
    off_r = real_offer(real_preview, {run for run, _ in keep})
    eng = {d["layer"]: d for d in eng_board["per_layer"]}
    print("\n########## 盘面 offer / take（支援卡人头）##########")
    print("参与 = 站位（站在这条命令位上的卡）+ 追加（`sub_command_partner_array`，"
          "引擎里没有这个机制）；同一张卡可以既算自己位、又追加进别条命令。")
    print("%-24s %7s %9s %9s %9s %9s %8s" % ("落子层", "手数/局", "参与/手", "站位/手",
                                             "追加/手", "≥2参与%", "取最满%"))
    for (kind, camp), a in sorted(real.items()):
        layer = "合宿" if camp == "1" else "非合宿"
        n = a["n"]
        part = a["h"] / n
        own = a["own"] / n
        print("%-24s %7.2f %9.3f %9.3f %9.3f %9.1f %8.1f" % (
            "真机 %s %s" % (layer, "稽古" if kind == "keiko" else "训练"),
            n / n_runs, part, own, part - own,
            100.0 * a["ge2t"] / n, 100.0 * a["mx"] / max(a["b"], 1)))
    for layer, tag in (("camp", "合宿"), ("non_camp", "非合宿")):
        e = eng.get(layer)
        if not e:
            continue
        n = e["train_decisions_per_run"]
        hist = e["chosen_hist_per_run"]
        hmean = sum(i * c for i, c in enumerate(hist)) / n if n else 0.0
        print("%-24s %7.2f %9.3f %9.3f %9.3f %9.1f %8.1f" % (
            "引擎 %s（训练+稽古混层）" % tag, n, hmean, hmean, 0.0,
            100.0 * e["chosen_ge2_share"], 100.0 * e["take_max_share"]))

    print("\n%-20s %8s %10s %10s %9s" % ("盘面层", "板/局", "最满位站位", "每板放卡", "条目/板"))
    hand_c = collections.Counter()
    for (kind, camp), a in real.items():
        hand_c[camp] += a["n"]
    off_w = {"0": [0.0, 0.0], "1": [0.0, 0.0]}
    for camp, s in off_r.items():
        w = hand_c[camp]
        off_w[camp][0] += w * s[1] / s[0]
        off_w[camp][1] += s[2] / s[0]
        print("%-20s %8.2f %10.3f %10.2f %9.2f" % (
            "真机 %s" % ("合宿" if camp == "1" else "非合宿"), s[0] / n_runs,
            s[1] / s[0], s[2] / s[0], s[3] / s[0]))
    r_hands = sum(hand_c.values()) / n_runs
    r_off_max = sum(off_w[c][0] for c in off_w) / max(sum(hand_c.values()), 1)
    r_cards = sum(off_w[c][1] * hand_c[c] for c in off_w) / max(sum(hand_c.values()), 1)
    e_dec = sum(e["decisions_per_run"] for e in eng.values())
    e_hands = sum(e["train_decisions_per_run"] for e in eng.values())
    e_off_max = sum(sum(i * c for i, c in enumerate(e["max_hist_per_run"])) / e["decisions_per_run"]
                    * e["train_decisions_per_run"] for e in eng.values()) / e_hands
    for camp in ("0", "1"):
        e = eng.get("camp" if camp == "1" else "non_camp")
        if not e:
            continue
        d = e["decisions_per_run"]
        print("%-20s %8.2f %10.3f %10.2f %9s" % (
            "引擎 %s" % ("合宿" if camp == "1" else "非合宿"), d,
            sum(i * c for i, c in enumerate(e["max_hist_per_run"])) / d,
            e["mean_slot_heads"] * 5.0, "5（固定）"))
    r_part = sum(a["h"] for a in real.values()) / max(r_hands * n_runs, 1)
    r_own = sum(a["own"] for a in real.values()) / max(r_hands * n_runs, 1)
    e_take = sum(sum(i * c for i, c in enumerate(e["chosen_hist_per_run"])) for e in eng.values()) \
        / e_hands
    print("\n  落子参与人头 真机 %.3f vs 引擎 %.3f = %.2fx（手/局 真机 %.2f vs 引擎 %.2f）"
          % (r_part, e_take, r_part / e_take, r_hands, e_hands))
    f_arr = r_off_max / e_off_max
    f_pick = (r_own / r_off_max) / (e_take / e_off_max)
    f_sub = r_part / r_own
    lg = [math.log(f_arr), math.log(f_pick), math.log(f_sub)]
    print("  乘法拆解 = 站位排布 %.3f × 选位增益 %.3f × 追加参与 %.3f = %.3f"
          % (f_arr, f_pick, f_sub, f_arr * f_pick * f_sub))
    print("  （落子站位/最满位：真机 %.2f vs 引擎 %.2f ⇒ 真机几乎必取最满，引擎只取到盘面的 %.0f%%）"
          % (r_own / r_off_max, e_take / e_off_max, 100.0 * e_take / e_off_max))
    print("  对数份额：站位 %.0f%%｜选位 %.0f%%｜追加 %.0f%%"
          % tuple(100.0 * x / sum(lg) for x in lg))
    print("  每板放卡（站位总数）真机加权 %.2f vs 引擎 %.2f"
          % (r_cards, sum(e["mean_slot_heads"] * 5.0 * e["train_decisions_per_run"]
                          for e in eng.values()) / e_hands))


def run_metric(real, eng, metric):
    u = UNITS[metric]
    print("\n########## 度量：%s ##########" % u)
    a = {}
    for kind, label in (("plain", "普通训练 home 腿"), ("keiko", "稽古 home 腿")):
        a[kind] = split(kind, real, eng, label, metric)
    gap = (a["plain"]["E"] + a["keiko"]["E"]) - (a["plain"]["R"] + a["keiko"]["R"])
    arr = a["plain"]["arr"] + a["keiko"]["arr"]
    frm = a["plain"]["frm"] + a["keiko"]["frm"]
    print("\n== 两条腿合并（%s）==" % u)
    print("缺口合计 %.0f %s/局 = 落位/手数 %.0f + 公式 %.0f（落位占比 %.0f%%）" % (
        gap, u, arr, frm, 100.0 * arr / (abs(arr) + abs(frm))))
    t = {}
    for kind, label in (("plain", "普通训练"), ("keiko", "稽古")):
        print("  [%s home 腿]" % label)
        t[kind] = split3(kind, real, eng, metric)
    print("  [两腿合并]（各 kind 单独算完再相加，桶不跨 kind 混）")
    print("  %-10s 总手数 %+8.0f｜人头分布 %+8.0f｜公式 %+8.0f  (合计 %+8.0f)"
          % (UNITS[metric],
             sum(x["hands"] for x in t.values()), sum(x["dist"] for x in t.values()),
             sum(x["form"] for x in t.values()),
             sum(x["E"] - x["R"] for x in t.values())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", default="out/train_leg_chosen_20261103.csv")
    ap.add_argument("--preview", default="out/train_leg_preview_20261103.csv")
    ap.add_argument("--engine", default="E:/HJM/_keileg/out_head4000/trainleg2_result.json")
    ap.add_argument("--engine-board",
                    default="E:/HJM/_keileg/out_head4000/trainboard_result.json")
    a = ap.parse_args()
    real, n_runs, dropped, keep = real_side(a.real)
    eng = engine_side(a.engine)
    print("真机完整局 run=%d（剔除段内训练手 <%d 的 %d 个 run）｜引擎 4,000 局" % (n_runs, MIN_HANDS, dropped))
    run_board(a.real, a.preview,
              json.load(open(a.engine_board, encoding="utf-8"))["train_board"],
              keep, n_runs)
    for metric in ("pt", "five"):
        run_metric(real, eng, metric)


if __name__ == "__main__":
    main()
