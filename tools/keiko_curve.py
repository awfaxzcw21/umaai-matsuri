#!/usr/bin/env python3
"""稽古（演目）每手 PT 的逐手曲线 —— 把「板预告」拆成两条腿，对着「实到」核。

从仓根跑：

    python tools/keiko_curve.py                  # 全部 run 的分层汇总
    python tools/keiko_curve.py --run 179        # 单局逐手（门禁锚点）
    python tools/keiko_curve.py --all            # 所有 run 的逐手明细
    python tools/keiko_curve.py --csv out/_keiko_curve_20261103.csv

一手来源 = `E:/HJM/jidianbeiAI0.22/out/hachimi_raw` 的 msgpack 报文（只读）。
口径：
  - 一手稽古 = 一条 `single_mode_exec_command_request_common`，
    `command_type == 1` 且 `command_id ∈ {5101..5105}`（速耐力根智，与 pt_ledger_scan 的 R2 同规则）。
  - pre = 该请求之前最后一个带稽古板的快照；post = 请求之后第一个同会话快照。
    窗口差分不跨请求（跨回合差分是已知陷阱）。
  - 两条腿都从**板上的预告**读：`home_info.command_info_array[cid].params_inc_dec_info_array`
    里 `target_type == 30` 的是训练腿，`festa_data_set.command_info_array[cid]` 同键的是演目腿。
    实到 = post.skill_point − pre.skill_point。
不产出任何系数、不改任何配置。
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import os
import statistics as st
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

FESTA = {5101: "速", 5102: "耐", 5103: "力", 5104: "根", 5105: "智"}
AXES = ("speed", "stamina", "power", "guts", "wiz")
# 大祭 五名成员的 chara_id（报文 `evaluation_info_array` 里 target_id 就是这些）
MEMBERS = {1009, 1029, 1128, 1096, 1024}


def leg_map(data, cid):
    """板上报文自报的逐 target_type 预告值：{holder: {target_type: 值}}，两腿分开。"""
    out = {}
    for holder in ("home_info", "festa_data_set"):
        acc, has = {}, False
        for c in ((data.get(holder) or {}).get("command_info_array")) or []:
            if c.get("command_id") != cid:
                continue
            has = True
            for e in c.get("params_inc_dec_info_array") or []:
                tt = e.get("target_type")
                acc[tt] = acc.get(tt, 0) + (e.get("value") or 0)
        out[holder] = acc if has else None
    return out


def assign_of(data):
    """每个演目分到的人：卡组位（target_id 1..6）/ 大祭成员 / 其它。"""
    fs = data.get("festa_data_set") or {}
    out = collections.defaultdict(lambda: [0, 0, 0])
    for e in fs.get("evaluation_info_array") or []:
        at = e.get("act_type")
        if not at:
            continue
        tid = e.get("target_id") or 0
        slot = out[at]
        if 1 <= tid <= 6:
            slot[0] += 1
        elif tid in MEMBERS:
            slot[1] += 1
        else:
            slot[2] += 1
    return {at: tuple(v) for at, v in out.items()}


def board_of(data, ci):
    """把「最近一拍 chara_info」压成 pre 快照（稽古板在不在都建，板上的两条腿先算好存着）。"""
    fs = data.get("festa_data_set") or {}
    info = fs.get("festa_info") or {}
    act_lv, act_advice, act_gauge = {}, {}, {}
    for a in fs.get("act_info_array") or []:
        at = a.get("act_type")
        act_lv[at] = a.get("level")
        act_advice[at] = a.get("advice_id")
        act_gauge[at] = (a.get("act_gauge"), a.get("need_act_gauge"))
    tli = {e.get("command_id"): e.get("level")
           for e in (ci.get("training_level_info_array") or [])}
    return {
        "key": (ci.get("single_mode_chara_id"), ci.get("start_time")),
        "turn": ci.get("turn"),
        "pt": ci.get("skill_point"),
        "five": [ci.get(k) for k in AXES],
        "caps": [ci.get("max_" + k) for k in AXES],
        "camp": info.get("summer_camp_remain_turn_num") or 0,
        "max_lv": info.get("max_act_level"),
        "step": info.get("act_training_step"),
        "act_lv": act_lv,
        "act_advice": act_advice,
        "act_gauge": act_gauge,
        "tli": tli,
        "has_board": any(c.get("command_id") in FESTA
                         for c in (fs.get("command_info_array") or [])),
        "legs": {cid: leg_map(data, cid) for cid in FESTA},
        "assign": assign_of(data),
    }


def pear(x, y):
    if len(x) < 2:
        return float("nan")
    mx, my = st.mean(x), st.mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5
    return num / den if den else float("nan")


def seg_of(turn):
    if turn <= 27:
        return "t1-27"
    if turn <= 48:
        return "t28-48"
    if turn <= 72:
        return "t49-72(含合宿)"
    return "t73+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="", help="只报这个 run 的逐手明细")
    ap.add_argument("--all", action="store_true", help="所有 run 都报逐手明细")
    ap.add_argument("--raw", default=PS.RAW)
    ap.add_argument("--csv", default="", help="把逐手导出 CSV（复核用）")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.raw, "*.bin")), key=PS.sort_key)
    last = None       # 最近一拍 chara_info（含板上两条腿）
    pending = None
    rows = []
    stats = collections.Counter()

    for path in files:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            stats["decode_fail"] += 1
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_notify_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            if hit is None:
                continue
            ch, common = PS.CHANNELS[hit], d[hit] or {}
            cid = common.get("command_id")
            if ch == "cmd" and common.get("command_type") == 1 and cid in FESTA and last is not None:
                lg = last["legs"][cid] or {}
                hm, ft = lg.get("home_info") or {}, lg.get("festa_data_set") or {}
                home_pt, festa_pt = hm.get(30), ft.get(30)
                stats["req_keiko"] += 1
                if not last["has_board"]:
                    stats["pre_no_board"] += 1
                if common.get("current_turn") not in (None, last["turn"]):
                    stats["turn_mismatch"] += 1
                if home_pt is None and festa_pt is None:
                    stats["req_no_legs"] += 1
                pending = {
                    "run": last["key"][0], "start": last["key"][1], "file": name,
                    "turn": last["turn"], "cid": cid, "axis": FESTA[cid],
                    "act_type": cid - 5100, "act_lv": last["act_lv"].get(cid - 5100),
                    "max_lv": last["max_lv"], "camp": last["camp"], "step": last["step"],
                    "advice": last["act_advice"].get(cid - 5100),
                    "gauge": last["act_gauge"].get(cid - 5100),
                    "lv_home": last["tli"].get(cid),
                    "lv_10x": last["tli"].get(cid - 5000),
                    "pt_home": home_pt, "pt_festa": festa_pt,
                    "main_home": hm.get(cid - 5100), "main_festa": ft.get(cid - 5100),
                    "n_deck": last["assign"].get(cid - 5100, (0, 0, 0))[0],
                    "n_member": last["assign"].get(cid - 5100, (0, 0, 0))[1],
                    "pt_pred": (home_pt or 0) + (festa_pt or 0),
                    "pt_before": last["pt"], "five_before": last["five"],
                    "caps_before": last["caps"],
                }
            else:
                pending = None      # 别的请求插进来 ⇒ 这一板没被执行，不配对
            continue
        data = d.get("data") or {}
        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        snap = board_of(data, ci)
        if snap["has_board"]:
            stats["boards"] += 1
        if pending is not None and snap["key"] == (pending["run"], pending["start"]):
            after = snap["pt"]
            if isinstance(after, int) and isinstance(pending["pt_before"], int):
                pending["pt_real"] = after - pending["pt_before"]
                pending["d_five"] = [b - a for a, b in zip(pending["five_before"], snap["five"])]
                rows.append(pending)
                stats["paired"] += 1
                pending = None
        last = snap

    if args.run:
        rows = [r for r in rows if str(r["run"]) == str(args.run)]

    print(f"== 稽古一手 {len(rows)} 手（带板快照 {stats['boards']}，配对 {stats['paired']}）==")
    if args.run or args.all:
        print("  run  turn cid 轴 演目Lv 教诲 训练Lv home腿 festa腿 预告 实到 差  Δ五维")
        for r in sorted(rows, key=lambda r: (r["run"], r["turn"])):
            ok = "" if r["pt_real"] == r["pt_pred"] else "  ≠"
            print(f"  {r['run']:<4} t{r['turn']:<4} {r['cid']} {r['axis']} "
                  f"{str(r['act_lv']):<6} {str(r['advice']):<5} {str(r['lv_10x']):<6} "
                  f"{str(r['pt_home']):<6} {str(r['pt_festa']):<7} {r['pt_pred']:<4} "
                  f"{r['pt_real']:<4} {r['pt_real'] - r['pt_pred']:<4}{ok} "
                  f"{('|'.join(str(x) for x in r['d_five']))}")

    def layer(title, keyf):
        print(f"\n== {title} ==")
        print("  层      n   home腿均  festa腿均  预告均  实到均  实到==预告  festa占预告")
        by = collections.defaultdict(list)
        for r in rows:
            by[keyf(r)].append(r)
        for k in sorted(by, key=lambda x: (str(x) == "None", x)):
            g = by[k]
            home = [r["pt_home"] or 0 for r in g]
            festa = [r["pt_festa"] or 0 for r in g]
            pred = [r["pt_pred"] for r in g]
            real = [r["pt_real"] for r in g]
            eq = sum(1 for r in g if r["pt_real"] == r["pt_pred"])
            share = (st.mean(festa) / st.mean(pred) * 100) if st.mean(pred) else 0
            print(f"  {str(k):<7} {len(g):<4} {st.mean(home):<9.1f} {st.mean(festa):<10.1f} "
                  f"{st.mean(pred):<8.1f} {st.mean(real):<7.1f} {eq}/{len(g):<7} {share:.1f}%")
        return by

    def ratio_table(title, keyf, cols):
        """cols = [(名字, 分子字段, 分母字段)]；每层报各列的比值中位数。"""
        print(f"\n== {title} ==")
        print("  层      n   " + "  ".join(f"{c[0]:<14}" for c in cols) + " 成员均 卡组位均")
        by = collections.defaultdict(list)
        for r in rows:
            by[keyf(r)].append(r)
        for k in sorted(by, key=lambda x: (str(x) == "None", x)):
            g = by[k]
            cells = []
            for _, num, den in cols:
                rs = sorted(float(r[num]) / r[den] for r in g
                            if isinstance(r.get(num), int) and isinstance(r.get(den), int) and r[den] > 0)
                cells.append(f"{st.median(rs):.3f}(n={len(rs)})" if rs else "-")
            nm = st.mean(r["n_member"] or 0 for r in g)
            nd = st.mean(r["n_deck"] or 0 for r in g)
            print(f"  {str(k):<7} {len(g):<4} " + "  ".join(f"{c:<14}" for c in cells)
                  + f" {nm:<6.2f} {nd:.2f}")

    if len(rows) >= 2:
        layer("按演目 Lv", lambda r: r["act_lv"])
        layer("按是否合宿（camp 剩余>0）", lambda r: "合宿" if r["camp"] > 0 else "非合宿")
        layer("按回合段", lambda r: seg_of(r["turn"]))
        layer("按训练腿有没有值（板上报文缺 home 腿的分开看）", lambda r: r["pt_home"] is None)
        ratio_table("演目腿 / 训练腿 的比值：SP 腿 与 主属性腿 同形吗（对照 201 v4 = 20%→170%）",
                    lambda r: r["act_lv"],
                    [("SP腿比", "pt_festa", "pt_home"), ("主属性腿比", "main_festa", "main_home")])
        ratio_table("按演目分到的大祭成员数（找 201 之外那一截的宿主）",
                    lambda r: r["n_member"],
                    [("SP腿比", "pt_festa", "pt_home"), ("主属性腿比", "main_festa", "main_home")])

        print("\n== 相关（只是同向变动，不构成公式）==")
        pred = [r["pt_pred"] for r in rows]
        real = [r["pt_real"] for r in rows]
        festa = [r["pt_festa"] or 0 for r in rows]
        home = [r["pt_home"] or 0 for r in rows]
        lv = [r["act_lv"] or 0 for r in rows]
        main_gain = [r["d_five"][AXES.index({"速": "speed", "耐": "stamina", "力": "power",
                                             "根": "guts", "智": "wiz"}[r["axis"]])] for r in rows]
        print(f"  r(预告, 实到) = {pear(pred, real):+.3f}   r(festa腿, 演目Lv) = {pear(festa, lv):+.3f}")
        print(f"  r(home腿, 主轴实到) = {pear(home, main_gain):+.3f}   r(实到, 回合) = {pear(real, [r['turn'] for r in rows]):+.3f}")
        print(f"  实到==预告：{sum(1 for r in rows if r['pt_real'] == r['pt_pred'])}/{len(rows)}"
              f"   实到范围 {min(real)}–{max(real)}   预告范围 {min(pred)}–{max(pred)}")

        print("\n== 逐局合计 ==")
        byrun = collections.defaultdict(list)
        for r in rows:
            byrun[r["run"]].append(r)
        print("  run   n   home合计  festa合计  预告合计  实到合计  每手实到")
        for k in sorted(byrun):
            g = byrun[k]
            ho = sum(r["pt_home"] or 0 for r in g)
            fe = sum(r["pt_festa"] or 0 for r in g)
            pr = sum(r["pt_pred"] for r in g)
            rl = sum(r["pt_real"] for r in g)
            print(f"  {k:<6} {len(g):<3} {ho:<10} {fe:<10} {pr:<9} {rl:<9} {rl / len(g):.1f}")

    if args.csv:
        os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
        cols = ["run", "start", "turn", "cid", "axis", "act_type", "act_lv", "max_lv",
                "camp", "step", "advice", "lv_home", "lv_10x", "gauge",
                "pt_home", "pt_festa", "main_home", "main_festa",
                "n_deck", "n_member", "pt_pred", "pt_real", "pt_before",
                "five_before", "d_five", "caps_before", "file"]
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in sorted(rows, key=lambda r: (r["run"], r["turn"])):
                w.writerow(["|".join(map(str, r[c])) if isinstance(r.get(c), list)
                            else r.get(c) for c in cols])
        print(f"\n（CSV → {args.csv}；out/ 在 .gitignore 里，别当交付物）")


if __name__ == "__main__":
    main()
