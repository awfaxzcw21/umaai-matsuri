"""单手可归因的收益表：不用跨回合差分，而是在**同一回合内**把
「带预告的 board 报文 P」和「其后第一条带 command_result 的结算报文 R」配对，
delta = R.five − P.five 就是这一手。

一手 = **两个加项**（本文件实测出来的口径，不是假设）：
  home 板 params_inc_dec_info_array = 训练本身（含支援卡、友情、Lv）
  festa 板 params_inc_dec_info_array = 大祭「作戦 / 合同練習 step」额外加的那一份（含体力回、四轴溢出的主要来源）
两者按 target_type 相加才是玩家看到的最终增量。

产出：
1. home+festa == 结算 的手数占比（引擎要不要把这两项分别算出来）；
2. 各加项由什么决定（训练 Lv / 参加人头 / act_step）。
"""

import collections
import csv
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
PK = ["speed", "stamina", "power", "guts", "wiz"]
MK = ["max_" + k for k in PK]
AX = ["速度", "耐力", "力量", "根性", "智力"]
TT = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 10: "vital", 30: "sp"}


def preds_of(arr):
    b = {}
    for c in arr or []:
        p = collections.Counter()
        for e in c.get("params_inc_dec_info_array") or []:
            p[TT.get(e.get("target_type"), "tt" + str(e.get("target_type")))] += e.get("value") or 0
        if not p:
            continue
        b[c.get("command_id")] = {"pred": dict(p), "level": c.get("level"),
                                 "partners": len(c.get("training_partner_array") or []),
                                 "failure_rate": c.get("failure_rate")}
    return b


def hands():
    """run -> turn -> (P 的 board, R 的结算)。P = 该回合最后一条带预告的报文，R = 其后第一条带 command_result 的。"""
    files = sorted(f for f in os.listdir(RAW) if f.endswith(".bin"))
    per = collections.defaultdict(dict)
    for f in files:
        try:
            d = decode_file(RAW + f)
        except Exception:
            continue
        data = d.get("data")
        if not isinstance(data, dict):
            continue
        ci = data.get("chara_info")
        if not isinstance(ci, dict) or not all(k in ci for k in MK):
            continue
        rid, t = ci.get("single_mode_chara_id"), ci.get("turn")
        if not isinstance(t, int):
            continue
        cr = data.get("command_result") or {}
        rec = {"file": f, "turn": t, "five": [ci[k] for k in PK], "caps": [ci[k] for k in MK],
               "vital": ci.get("vital"), "sp": ci.get("skill_point"),
               "motivation": ci.get("motivation"),
               "effects": tuple(sorted(set(ci.get("chara_effect_id_array") or []))),
               "act_step": ((data.get("festa_data_set") or {}).get("festa_info") or {}).get("act_training_step"),
               "board": preds_of((data.get("home_info") or {}).get("command_info_array")),
               "festa_board": preds_of((data.get("festa_data_set") or {}).get("command_info_array")),
               "cmd": cr.get("command_id"), "sub_id": cr.get("sub_id"), "state": cr.get("result_state")}
        slot = per[rid].setdefault(t, {"P": None, "R": None})
        if rec["board"] or rec["festa_board"]:
            slot["P"] = rec          # 最后一条带预告的为准
        if slot["P"] is not None and slot["R"] is None and rec["cmd"] is not None and rec is not slot["P"]:
            slot["R"] = rec          # P 之后第一条带结算的
    out = []
    for rid, ts in sorted(per.items()):
        for t in sorted(ts):
            slot = ts[t]
            P, R = slot["P"], slot["R"]
            if not (P and R):
                continue
            e_home = P["board"].get(R["cmd"])
            e_festa = P["festa_board"].get(R["cmd"])
            entry = e_home or e_festa
            if entry is None:
                continue           # 这一手不在预告板里（比赛/外出等），跳过
            total = collections.Counter()
            for e in (e_home, e_festa):
                if e:
                    total.update(e["pred"])
            out.append({"run": rid, "turn": t, "file": R["file"], "board_file": P["file"],
                        "command": R["cmd"], "sub_id": R["sub_id"], "state": R["state"],
                        "level": entry["level"], "partners": entry["partners"],
                        "failure_rate": entry["failure_rate"], "act_step": P["act_step"],
                        "mot": P["motivation"], "vital0": P["vital"],
                        "pred": dict(total), "pred_home": dict(e_home["pred"]) if e_home else {},
                        "pred_festa": dict(e_festa["pred"]) if e_festa else {}, "before": P["five"], "after": R["five"],
                        "caps": R["caps"], "effects": list(P["effects"]),
                        "delta": [R["five"][i] - P["five"][i] for i in range(5)],
                        "d_vital": (R["vital"] or 0) - (P["vital"] or 0),
                        "d_sp": (R["sp"] or 0) - (P["sp"] or 0),
                        "d_mot": (R["motivation"] or 0) - (P["motivation"] or 0)})
    return out


def main():
    hs = hands()
    print("单手可归因的手数: " + str(len(hs)) + "（run " + str(len({h["run"] for h in hs})) + " 个）")

    dirty = [h for h in hs if any(h["delta"][i] != 0 for i in range(5) if i not in h["pred"])]
    print("污染检查：预告里没提到的轴却动了的手 " + str(len(dirty)) + " / " + str(len(hs))
          + "（非 0 说明这手里还夹了别的东西，别拿来算乘区）")

    def match(getter):
        return sum(1 for h in hs
                   if all(abs(h["delta"][i] - getter(h).get(i, 0)) < 0.5 for i in range(5)))

    home_only, both = match(lambda h: h["pred_home"]), match(lambda h: h["pred"])
    print("五轴逐轴全等：只用 home 预告 " + str(home_only) + "（" + f"{100.0 * home_only / len(hs):5.1f}"
          + "%）  home+festa 合计 " + str(both) + "（" + f"{100.0 * both / len(hs):5.1f}" + "%）")

    print("\n=== 结算 / 预告 的倍数（只取该手预告的主轴 = 预告里最大的正增量轴）")
    byc = collections.defaultdict(list)
    for h in hs:
        nums = {k: v for k, v in h["pred"].items() if isinstance(k, int)}
        if not nums:
            continue
        mi = max(nums, key=nums.get)
        h["main_axis"] = mi
        byc[h["command"]].append(h)
    for c in sorted(byc, key=lambda x: -len(byc[x])):
        rs = byc[c]
        mi = rs[0]["main_axis"]
        if len({r["main_axis"] for r in rs}) > 1:
            mi = collections.Counter(r["main_axis"] for r in rs).most_common(1)[0][0]
        pv = [r["pred"].get(mi, 0) for r in rs]
        av = [r["delta"][mi] for r in rs]
        rat = [a / p for a, p in zip(av, pv) if p > 0]
        eq = sum(1 for a, p in zip(av, pv) if a == p)
        print("  " + str(c).rjust(4) + " " + AX[mi] + " n=" + str(len(rs)).rjust(3)
              + " 逐手相等 " + f"{100.0 * eq / len(rs):5.1f}" + "%"
              + "  预告 " + f"{sum(pv) / len(pv):6.1f}" + " → 结算 " + f"{sum(av) / len(av):6.1f}"
              + "  倍率 中位 " + f"{sorted(rat)[len(rat) // 2]:.3f}" + " "
              + f"p10 {sorted(rat)[max(0, int(0.1 * len(rat)) - 1)]:.2f}"
              + f" p90 {sorted(rat)[min(len(rat) - 1, int(0.9 * len(rat)))]:.2f}")

    print("\n=== 倍率对候选解释变量的响应（主轴倍率 = 结算/预告）")
    for field in ("level", "partners", "act_step", "state"):
        g = collections.defaultdict(list)
        for r in (x for v in byc.values() for x in v):
            nums = {k: v for k, v in r["pred"].items() if isinstance(k, int)}
            if not nums:
                continue
            mi = max(nums, key=nums.get)
            p = r["pred"].get(mi, 0)
            if p > 0:
                g[r[field]].append(r["delta"][mi] / p)
        line = "  " + field.ljust(9)
        for k in sorted(g, key=lambda x: (x is None, x)):
            v = g[k]
            line += " " + str(k) + ":" + f"{sum(v) / len(v):.2f}" + "(" + str(len(v)) + ")"
        print(line)

    print("\n=== 体力 / 技能点：结算==预告吗")
    for key, dk in (("vital", "d_vital"), ("sp", "d_sp")):
        eq = sum(1 for h in hs if h["pred"].get(key) == h[dk])
        dv = [h[dk] - h["pred"].get(key, 0) for h in hs if key in h["pred"]]
        print("  " + key + " n=" + str(len(dv)) + " 逐手相等 " + f"{100.0 * eq / len(hs):5.1f}"
              + "%  (结算−预告) 均值 " + f"{sum(dv) / len(dv):+7.2f}" + " 中位 "
              + f"{sorted(dv)[len(dv) // 2]:+6.1f}")

    print("\n=== 非预告轴的溢出（这手练速，别的轴涨了多少）：按 command 分组")
    for c in sorted(byc, key=lambda x: -len(byc[x])):
        rs = byc[c]
        mi = collections.Counter(r["main_axis"] for r in rs).most_common(1)[0][0]
        sp = [sum(r["delta"][i] for i in range(5) if i != mi) for r in rs]
        pred_sp = [sum(v for k, v in r["pred"].items() if isinstance(k, int) and k != mi) for r in rs]
        print("  " + str(c).rjust(4) + " n=" + str(len(rs)).rjust(3) + " 实际溢出 "
              + f"{sum(sp) / len(sp):+6.1f}" + " 预告溢出 " + f"{sum(pred_sp) / len(pred_sp):+6.1f}")

    print("\n=== 钳位检验：预告增量大过剩余空间（上限−练前值）时，结算怎么走")
    n_over = n_clip = n_free = n_other = 0
    ex = []
    for h in hs:
        for i in range(5):
            rem = h["caps"][i] - h["before"][i]
            p = h["pred"].get(i, 0)
            if p <= rem:
                continue
            n_over += 1
            if h["delta"][i] == rem:
                n_clip += 1
            elif h["delta"][i] == p:
                n_free += 1
                if len(ex) < 5:
                    ex.append(h)
            else:
                n_other += 1
    print("  预告>剩余 的(手,轴)组合 " + str(n_over) + " 个：截到剩余 " + str(n_clip)
          + " / 完全不截（照预告给，练完就超上限） " + str(n_free) + " / 其它 " + str(n_other))
    for h in ex:
        print("    例 command " + str(h["command"]) + " t" + str(h["turn"]) + " 练前 " + str(h["before"])
              + " 上限 " + str(h["caps"]) + " 预告 " + str(h["pred"]) + " 结算 " + str(h["delta"]))

    print("\n=== 剩余空间衰减（干净单手，只取该手预告主轴；组内 = 同一 run 同一 command）")
    pts = collections.defaultdict(list)
    for h in hs:
        nums = {k: v for k, v in h["pred"].items() if isinstance(k, int)}
        if not nums or h["state"] != 2:
            continue
        mi = max(nums, key=nums.get)
        pts[(h["run"], h["command"], mi)].append((h["caps"][mi] - h["before"][mi], h["delta"][mi]))
    allp = [x for v in pts.values() for x in v]
    n = len(allp)
    def rank(v):
        return [sorted(v).index(x) for x in v]
    xs = [r for r, d in allp]
    ys = [d for r, d in allp]
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    rho = sum((rx[i] - mx) * (ry[i] - my) for i in range(n)) / den if den else 0.0
    means = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - means[0]) * (ys[i] - means[1]) for i in range(n))
    sxx = sum((x - means[0]) ** 2 for x in xs)
    beta = cov / sxx if sxx else 0.0
    resid = [ys[i] - (means[1] + beta * (xs[i] - means[0])) for i in range(n)]
    se = (sum(r * r for r in resid) / (n - 2) / sxx) ** 0.5 if sxx else 0.0
    print("  Pooled（含跨手差异，仅参照）：n=" + str(n) + " ρ=" + f"{rho:+.3f}"
          + " β(OLS)=" + f"{beta:+.5f}" + "±" + f"{se:.5f}")
    tot_b, tot_se, tot_n = 0.0, 0.0, 0
    for k, v in sorted(pts.items(), key=lambda kv: -len(kv[1])):
        if len(v) < 8 or len({r for r, d in v}) < 3:
            continue
        m = len(v)
        mr = sum(r for r, d in v) / m
        md = sum(d for r, d in v) / m
        num = sum((r - mr) * (d - md) for r, d in v)
        sxxg = sum((r - mr) ** 2 for r, d in v)
        if not sxxg:
            continue
        b = num / sxxg
        res = [d - (md + b * (r - mr)) for r, d in v]
        s = (sum(x * x for x in res) / (m - 2) / sxxg) ** 0.5
        tot_b += b * m
        tot_se += (s * m) ** 2
        tot_n += m
        print("    run " + str(k[0]) + " cmd " + str(k[1]) + " " + AX[k[2]] + " n=" + str(m).rjust(3)
              + " 剩余 " + f"{min(r for r, d in v):5.0f}–{max(r for r, d in v):5.0f}"
              + " β=" + f"{b:+.5f}" + "±" + f"{s:.5f}")
    if tot_n:
        print("  组内加权 β=" + f"{tot_b / tot_n:+.5f}" + "±" + f"{(tot_se ** 0.5) / tot_n:.5f}"
              + "  （n=" + str(tot_n) + "，这才是「剩余空间要不要进收益公式」的判据）")

    ok = [h for h in hs if h["state"] == 2 and max(h["before"]) < 1200]
    print(chr(10) + "=== 喂给引擎的收益表（只用练前五轴都<1200 的未压缩手 " + str(len(ok)) + "/"
          + str(sum(1 for h in hs if h["state"] == 2)) + " 手）：home 项与 festa 项分开设")
    for tag, sel in (("单独练", lambda c: c < 5100), ("合体练", lambda c: c >= 5100)):
        print("  -- " + tag)
        g = collections.defaultdict(list)
        for h in hs:
            # 只留「五轴都还没过 1200」的手：混合了软上限压缩的手会把高 Lv 那几行压低（Lv3-5 全是后期手）
            if not sel(h["command"]) or h["state"] != 2 or max(h["before"]) >= 1200:
                continue
            nums = {k: v for k, v in h["pred"].items() if isinstance(k, int)}
            if not nums:
                continue
            mi = max(nums, key=nums.get)
            g[(h["level"], h["partners"], h["mot"])].append(
                (h["pred_home"].get(mi, 0), sum(v for k, v in h["pred_festa"].items() if isinstance(k, int)),
                 sum(h["delta"]), h["pred"].get("sp", 0), h["pred"].get("vital", 0)))
        for k in sorted(g):
            v = g[k]
            print("    Lv" + str(k[0]) + " 人头" + str(k[1]) + " 干劲" + str(k[2]) + " n=" + str(len(v)).rjust(3)
                  + "  home主轴 " + f"{sum(x[0] for x in v) / len(v):6.1f}"
                  + "  festa五维和 " + f"{sum(x[1] for x in v) / len(v):6.1f}"
                  + "  五维和计 " + f"{sum(x[2] for x in v) / len(v):6.1f}"
                  + "  SP " + f"{sum(x[3] for x in v) / len(v):6.1f}"
                  + "  体力 " + f"{sum(x[4] for x in v) / len(v):+6.1f}")

    print(chr(10) + "=== 1200 软上限逐手核对（引擎规则 below + (gain-below)/2，below = min(gain, max(0, 1200-pre))）")
    # 每格的「名义增量」取该格练前<1200 那批手的最大值（未被压缩的那一档），不拟合任何系数
    mains = [h for h in hs if h.get("main_axis") is not None and h["state"] == 2]
    cell_hands = collections.defaultdict(list)
    for h in mains:
        cell_hands[(h["run"], h["command"], h["level"], h["partners"], h["mot"])].append(h)
    good, bad = 0, []
    for k, v in cell_hands.items():
        mi = collections.Counter(x["main_axis"] for x in v).most_common(1)[0][0]
        raw = [x["pred_home"].get(mi, 0) for x in v if x["before"][mi] < 1200]
        if not raw:
            continue
        nom = max(raw)
        for x in v:
            below = min(nom, max(0, 1200 - x["before"][mi]))
            p_rule = below + (nom - below) // 2
            actual = x["pred_home"].get(mi, 0)
            if p_rule == actual:
                good += 1
            else:
                x["rule_pred"], x["nominal"], x["axis"] = p_rule, nom, mi
                bad.append(x)
    tot = good + len(bad)
    print("  逐手命中 " + str(good) + " / " + str(tot) + "（" + f"{100.0 * good / tot:5.1f}"
          + "%）  未命中 " + str(len(bad)) + " 手")
    d = collections.Counter(x["pred_home"].get(x["axis"], 0) - x["rule_pred"] for x in bad)
    print("  差值分布(实际-规则): " + str(sorted(d.items(), key=lambda kv: -kv[1])[:8]))
    low = [x for x in bad if x["pred_home"].get(x["axis"], 0) < x["rule_pred"]]
    high = [x for x in bad if x["pred_home"].get(x["axis"], 0) > x["rule_pred"]]
    print("  实际低于规则 " + str(len(low)) + " 手 / 实际高于规则 " + str(len(high))
          + " 手（高于的多是同一格内 buff 随回合变长，名义最大值取早了）")
    n_clamp = sum(1 for x in low if x["before"][x["axis"]] + x["pred"].get(x["axis"], 0) >= x["caps"][x["axis"]])
    print("  实际低于规则的手里，练完正好顶到上限（=钳位造成的，不是软上限） " + str(n_clamp)
          + " / " + str(len(low)) + " 手")
    for x in low[:6]:
        print("    例 cmd " + str(x["command"]) + " Lv" + str(x["level"]) + " 人头" + str(x["partners"])
              + " t" + str(x["turn"]) + " 练前 " + str(x["before"][x["axis"]]) + " 上限 "
              + str(x["caps"][x["axis"]]) + " 格内名义 " + str(x["nominal"]) + " 规则 "
              + str(x["rule_pred"]) + " 实际 " + str(x["pred_home"].get(x["axis"], 0)))

    print(chr(10) + "=== 结算后正好落在上限上的 (手,轴) 组合数")
    n_at = sum(1 for h in hs for i in range(5) if h["delta"][i] > 0 and h["after"][i] == h["caps"][i])
    print("  " + str(n_at) + " 个（共 " + str(len(hs)) + " 手，说明钳位这条在真机里多久被触发一次）")
    with open("out/_hand_gain.csv", "w", newline="", encoding="utf-8-sig") as fh:
        keys = ["run", "turn", "command", "level", "partners", "act_step", "state",
                "main_axis", "pred", "pred_home", "pred_festa", "delta", "d_vital", "d_sp",
                "mot", "vital0", "before", "caps", "file"]
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        for r in hs:
            w.writerow({k: (json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], (list, dict)) else r.get(k))
                        for k in keys})
    json.dump(hs, open("out/_hand_gain.json", "w", encoding="utf-8"), ensure_ascii=False)
    print("\n落盘 out/_hand_gain.csv / out/_hand_gain.json")


if __name__ == "__main__":
    main()
