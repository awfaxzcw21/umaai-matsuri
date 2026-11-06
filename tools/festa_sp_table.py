#!/usr/bin/env python3
"""#92 第 1 步：把真机 festa 板的 `tt30`（SP 加项）逐手抽成一张表，
每行都带上「当时能看见的一切」——演目 Lv、该轴训练 Lv、教诲 id、人头与彩圈、
卡组（id／凸／exp）、合宿步、体力、干劲、上限档，以及该手**实际到账**的 ΔSP。

这不是拟合：先把一手数据的自变量全部登记下来，再看 6.6× 斜率与 2–3× 卡组散布
分别落在哪一列上。字段路径全部来自 `tools/festa_field_census.py` 的普查结果。

    python tools/festa_sp_table.py --out out/festa_sp_hands.jsonl
    python tools/festa_sp_table.py --report out/festa_sp_hands.jsonl
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
from msgpack_lite import decode_file  # noqa: E402

RAW = "E:/HJM/jidianbeiAI0.22/out/hachimi_raw/"
AX = ["速度", "耐力", "力量", "根性", "智力"]
# 稽古命令号 → 轴（一手：剧本 15 报文里只有 5101..5105 这一套训练命令号）
KEIKO = {5101 + i: i for i in range(5)}
# 普通训练命令号 → 轴（scen15 有 101/102/103/105/106，104 无行）
HOME = {101: 0, 102: 1, 103: 2, 105: 3, 106: 4}
PK = ["speed", "stamina", "power", "guts", "wiz"]


def tts(arr):
    out = {}
    for e in arr or []:
        out[str(e.get("target_type"))] = (out.get(str(e.get("target_type")), 0.0)
                                          + (e.get("value") or 0))
    return out


def scan(files):
    """run → turn → {"P": 预告快照, "R": 结算快照}，与 hand_gain 同一配对口径
    （P＝该回合最后一条带预告的；R＝P 之后第一条带 command_result 的）。"""
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
        if not isinstance(ci, dict) or not all(k in ci for k in PK):
            continue
        rid, t = ci.get("single_mode_chara_id"), ci.get("turn")
        if not isinstance(t, int):
            continue
        fds = data.get("festa_data_set") or {}
        cr = data.get("command_result") or {}
        rec = {
            "file": f, "turn": t,
            "sp": ci.get("skill_point"),
            "five": [ci.get(k) for k in PK],
            "caps": [ci.get("max_" + k) for k in PK],
            "vital": ci.get("vital"), "motivation": ci.get("motivation"),
            "effects": sorted(set(ci.get("chara_effect_id_array") or [])),
            "deck": sorted([[c.get("support_card_id"), c.get("position"),
                             c.get("limit_break_count"), c.get("exp")]
                            for c in (ci.get("support_card_array") or [])]),
            "eval": [[e.get("training_partner_id"), e.get("evaluation"),
                      e.get("target_id"), e.get("is_appear"), e.get("story_step")]
                     for e in (ci.get("evaluation_info_array") or [])],
            "acts": {a.get("act_type"): {"lv": a.get("level"), "gauge": a.get("act_gauge"),
                                         "need": a.get("need_act_gauge"),
                                         "advice": a.get("advice_id"),
                                         "state": a.get("act_training_state"),
                                         "f_out": a.get("friend_outing_gain_act_level")}
                     for a in (fds.get("act_info_array") or [])},
            "finfo": fds.get("festa_info") or {},
            "home": {c.get("command_id"): {"lv": c.get("level"),
                                           "partners": c.get("training_partner_array") or [],
                                           "sub": c.get("sub_command_partner_array") or [],
                                           "fail": c.get("failure_rate"),
                                           "tt": tts(c.get("params_inc_dec_info_array"))}
                     for c in ((data.get("home_info") or {}).get("command_info_array") or [])},
            "keiko": {c.get("command_id"): {"tt": tts(c.get("params_inc_dec_info_array")),
                                            "gauge_pred": [[g.get("act_type"), g.get("gain_act_gauge")]
                                                          for g in (c.get("predict_gain_act_gauge_array") or [])]}
                      for c in (fds.get("command_info_array") or [])
                      if c.get("command_id") in KEIKO},
            "cmd": cr.get("command_id"), "state": cr.get("result_state"),
        }
        # ★两个板分开留：稽古板（510x）与 home 板（101/102…）常出现在**不同**报文里，
        # 只留「最后一条带板的」会把稽古板冲掉（上一版就是这么只出 5 手）。
        slot = per[rid].setdefault(t, {"Pk": None, "Ph": None, "R": None})
        if rec["keiko"]:
            slot["Pk"] = rec
        if rec["home"]:
            slot["Ph"] = rec
        if (slot["Pk"] or slot["Ph"]) and slot["R"] is None and rec["cmd"] is not None                 and rec not in (slot["Pk"], slot["Ph"]):
            slot["R"] = rec
    return per


def rows(per, want_realized=True):
    out = []
    for rid, ts in sorted(per.items()):
        for t in sorted(ts):
            Pk, Ph, R = ts[t]["Pk"], ts[t]["Ph"], ts[t]["R"]
            if Pk is None:
                continue
            P = Pk
            # 训练 Lv / 人头来自 home 板同轴那一格；两板可能在不同报文里
            Phome = (Ph or Pk)["home"]
            for cid, k in P["keiko"].items():
                ai = KEIKO[cid]
                # 510x ↔ 演目：报文自己会说（predict_gain_act_gauge_array 里的 act_type）
                # 稽古板上的 gauge_pred 实测**恒为空**（2,177/2,177），拿不到就直接按号序取：
                # 5101..5105 ↔ act_type 1..5，与引擎 mccli 里 TRAIN/ACT 两张同序表一致，
                # 也和面板 `_state_t30.json` 的 acts[]（1=流鏑馬=速度 … 5=飛駆=智力）一致。
                act_type = next((g[0] for g in k["gauge_pred"] if g[0] is not None), ai + 1)
                act = P["acts"].get(act_type) if act_type is not None else None
                h = Phome.get({0: 101, 1: 102, 2: 103, 3: 105, 4: 106}[ai]) or {}
                d_sp = None
                fail_state = None
                if R is not None and R["cmd"] == cid and R is not Pk and want_realized:
                    d_sp = (R["sp"] or 0) - (P["sp"] or 0)
                    fail_state = R["state"]
                partners = h.get("partners") or []
                evmap = {e[0]: e[1] for e in P["eval"]}
                out.append({
                    "run": rid, "turn": t, "axis": ai, "axis_name": AX[ai],
                    "command": cid, "act_type": act_type,
                    "act_lv": (act or {}).get("lv"), "act_gauge": (act or {}).get("gauge"),
                    "act_need": (act or {}).get("need"), "advice": (act or {}).get("advice"),
                    "act_state": (act or {}).get("state"),
                    "train_lv": h.get("lv"), "fail_rate": h.get("fail"),
                    "n_partners": len(partners), "partners": partners,
                    "sub_partners": h.get("sub") or [],
                    "n_shining": sum(1 for p in partners if (evmap.get(p) or 0) >= 80),
                    "eval_vals": [evmap.get(p) for p in partners],
                    "tt30": k["tt"].get("30"), "tt_home_30": h.get("tt", {}).get("30"),
                    "tt_axis": k["tt"].get(str(ai + 1)),
                    "tt_all_festa": k["tt"],
                    "tt_all_home": h.get("tt"),
                    "vital": P["vital"], "motivation": P["motivation"],
                    "max_act_level": P["finfo"].get("max_act_level"),
                    "act_step": P["finfo"].get("act_training_step"),
                    "camp": P["finfo"].get("summer_camp_remain_turn_num"),
                    "sp_before": P["sp"], "d_sp": d_sp, "result_state": fail_state,
                    "deck": P["deck"], "deck_n": len(P["deck"]),
                    "effects": P["effects"],
                })
    return out


def report(path):
    hs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    print(f"逐手行数 {len(hs)}｜run {len({h['run'] for h in hs})} 个"
          f"｜带实际到账 {sum(1 for h in hs if h['d_sp'] is not None)} 手")
    ok = [h for h in hs if h["tt30"] is not None]
    print(f"板上带 tt30 的手 {len(ok)}")
    noact = sum(1 for h in ok if h["act_lv"] is None)
    print(f"  其中拿不到演目 Lv 的 {noact}（510x 的 gauge_pred 为空 ⇒ 该手没预告涨 gauge）")

    print("\n=== tt30 中位：按 (轴, 演目Lv)（演目 Lv 已知的手）")
    byc = collections.defaultdict(list)
    for h in ok:
        if h["act_lv"] is not None:
            byc[(h["axis_name"], h["act_lv"])].append(h["tt30"])
    print(f"{'轴':<5}{'Lv':>3}{'n':>6}{'中位':>7}{'p10':>6}{'p90':>6}{'vs L1':>8}")
    for ai in range(5):
        lvs = sorted(lv for (a, lv) in byc if a == AX[ai])
        if not lvs:
            continue
        base = None
        for lv in lvs:
            v = byc[(AX[ai], lv)]
            if len(v) < 8:
                continue
            v = sorted(v)
            med = v[len(v) // 2]
            if base is None:
                base = med
            print(f"{AX[ai]:<5}{lv:>3}{len(v):>6}{med:>7.0f}{v[int(.1*len(v))]:>6.0f}"
                  f"{v[min(len(v)-1, int(.9*len(v)))]:>6.0f}{med/base:>8.2f}")

    print("\n=== 同一 (轴, 演目Lv, 训练Lv) 格子里，跨 run 的散布（卡组因子在哪）")
    cell = collections.defaultdict(lambda: collections.defaultdict(list))
    for h in ok:
        if h["act_lv"] is None or h["train_lv"] is None:
            continue
        cell[(h["axis_name"], h["act_lv"], h["train_lv"])][h["run"]].append(h["tt30"])
    rows = []
    for key, runs in cell.items():
        if len(runs) < 3:
            continue
        meds = sorted(sum(runs[r]) / len(runs[r]) for r in runs)
        rows.append((key, len(runs), meds[0], meds[-1], meds[-1] / max(meds[0], 1e-9)))
    rows.sort(key=lambda x: -x[4])
    print(f"{'轴':<5}{'演目Lv':>7}{'训练Lv':>7}{'run数':>7}{'最低格均值':>12}{'最高格均值':>12}{'极差比':>8}")
    for key, nr, lo, hi, ratio in rows[:14]:
        print(f"{key[0]:<5}{key[1]:>7}{key[2]:>7}{nr:>7}{lo:>12.1f}{hi:>12.1f}{ratio:>8.2f}")
    print(f"（格子总数 {len(cell)}，其中 ≥3 个 run 的 {len(rows)} 个）")

    print("\n=== 斜率与截距拆开看：同一个 run 内的演目 Lv 斜率 vs 跨 run 的水位")
    print("    （跨 Lv 的中位表混着卡组，不能当斜率；这里只在**同一个 run、同一根轴**内比）")
    print(f"{'run':>8} {'轴':<4} {'覆盖Lv':>10} {'低Lv中位':>8} {'高Lv中位':>8} {'run内斜率':>9}")
    slopes = collections.defaultdict(list)
    for ax in AX:
        for rid in sorted({h['run'] for h in ok if h['axis_name'] == ax and h['act_lv'] is not None}):
            rs = [h for h in ok if h['axis_name'] == ax and h['run'] == rid and h['act_lv'] is not None]
            bylv = collections.defaultdict(list)
            for h in rs:
                bylv[h['act_lv']].append(h['tt30'])
            lvs = sorted(lv for lv, v in bylv.items() if len(v) >= 3)
            if len(lvs) < 4:
                continue
            lo, hi = lvs[0], lvs[-1]
            mlo = sum(bylv[lo]) / len(bylv[lo])
            mhi = sum(bylv[hi]) / len(bylv[hi])
            if mlo <= 0:
                continue
            sl = mhi / mlo
            slopes[ax].append((rid, lo, hi, mlo, mhi, sl))
            print(f"{rid:>8} {ax:<4} {f'L{lo}-L{hi}':>10} {mlo:>8.1f} {mhi:>8.1f} {sl:>9.2f}")
    print()
    for ax in AX:
        v = sorted(x[5] for x in slopes[ax])
        if not v:
            continue
        print(f"  {ax}：run 内斜率 中位 {v[len(v)//2]:.2f}×  "
              f"[{v[0]:.2f}, {v[-1]:.2f}]（{len(v)} 个 run）")
    print("  ⇒ 若 run 内斜率与跨 run 水位是两个分开的量，"
          "那「6.6×」就不是一个可以整列套用的乘数，得分别找载体。")
    print(f"\n  截距（低 Lv 端水位）跨 run 散布：")
    for ax in AX:
        base = sorted(x[3] for x in slopes[ax])
        if len(base) >= 3:
            print(f"    {ax}：{base[0]:.1f} / {base[len(base)//2]:.1f} / {base[-1]:.1f}"
                  f"（最低/中位/最高，n={len(base)}）⇒ 水位差 {base[-1]/max(base[0],1e-9):.1f}×")

    print("\n=== 相邻演目 Lv 的阶梯（同一个 run 内 l→l+1 的中位比，跨度无关，才是可比形状）")
    lad = collections.defaultdict(list)
    for ax in AX:
        for rid in {h['run'] for h in ok}:
            bylv = collections.defaultdict(list)
            for h in ok:
                if h['axis_name'] == ax and h['run'] == rid and h['act_lv'] is not None:
                    bylv[h['act_lv']].append(h['tt30'])
            for l in sorted(bylv):
                if l + 1 in bylv and len(bylv[l]) >= 3 and len(bylv[l + 1]) >= 3:
                    m0 = sum(bylv[l]) / len(bylv[l])
                    m1 = sum(bylv[l + 1]) / len(bylv[l + 1])
                    if m0 > 0:
                        lad[(ax, l)].append(m1 / m0)
    print(f"{'轴':<5}" + "".join(f"  L{l}→{l+1}" for l in range(1, 10)))
    for ax in AX:
        row = []
        for l in range(1, 10):
            v = sorted(lad[(ax, l)])
            row.append(f"{v[len(v)//2]:>7.2f}" if v else "      -")
        ns = [len(lad[(ax, l)]) for l in range(1, 10)]
        print(f"{ax:<5}" + "".join(row) + f"   （配对数 {sum(1 for x in ns if x)} 档，最多 {max(ns) if ns else 0}/档）")
    allv = [x for l in range(1, 10) for ax in AX for x in lad[(ax, l)]]
    if allv:
        allv.sort()
        print(f"  全体阶梯：中位 {allv[len(allv)//2]:.2f}×  p25 {allv[len(allv)//4]:.2f}  "
              f"p75 {allv[3*len(allv)//4]:.2f}  n={len(allv)}")
        print("  ⇒ 判据：阶梯大致**恒定**＝乘法（每加一级乘一次）；"
              "阶梯随 Lv **变大**＝凸（加法项本身在涨，如 109 那列 0/0/0/10/20/30/50/100/120/120）；"
              "**变小**＝饱和。")

    print("\n=== 到账 vs 板上 tt30：这一格到底逐手兑付还是期望")
    m = [h for h in ok if h["d_sp"] is not None]
    eq = sum(1 for h in m if h["d_sp"] == h["tt30"])
    print(f"  可配对 {len(m)} 手，ΔSP == tt30 的 {eq}（{100*eq/max(len(m),1):.1f}%）")
    rat = sorted(h["d_sp"] / h["tt30"] for h in m if h["tt30"])
    if rat:
        print(f"  ΔSP/tt30 中位 {rat[len(rat)//2]:.2f} p10 {rat[int(.1*len(rat))]:.2f} "
              f"p90 {rat[min(len(rat)-1, int(.9*len(rat)))]:.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="out/festa_sp_hands.jsonl")
    ap.add_argument("--n", type=int, default=0, help="只扫最后 N 条报文（0＝全部）")
    ap.add_argument("--report", default="")
    a = ap.parse_args()
    if a.report:
        report(a.report)
        return
    files = sorted(f for f in os.listdir(RAW) if f.endswith(".bin"))
    if a.n:
        files = files[-a.n:]
    print(f"扫 {len(files)} 条报文…", flush=True)
    per = scan(files)
    hs = rows(per)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for h in hs:
            f.write(json.dumps(h, ensure_ascii=False) + "\n")
    print(f"写出 {len(hs)} 手 → {a.out}")
    report(a.out)


if __name__ == "__main__":
    main()
