#!/usr/bin/env python3
"""稽古「打空率」口径对账：同一批稽古手，换三种 post 取法，看 1.5% 和 13% 谁是真的。

背景：`docs/PT缺口逐源_20261106.md` §11.3 用 `tools/keiko_curve.py` 的逐手 CSV 说
「真机 turn≥30 的 195 手只有 3 手 PT≤0 ＝ 1.5%」；本会话另一次随手复算得到 ~13%。
两个数不可能同时成立，差别只在 **post 那一拍怎么取**。

三种 post（pre 完全相同，都取请求前最后一拍的 `chara_info.skill_point`）：
  A `samirun`  ＝ 请求之后**同一局**的第一拍带 turn 的快照 ⇒ keiko_curve 的口径
  B `same_turn`＝ 同一局、`turn ==` 请求回合的那一拍 ＝ 「回合还没推进」的未结算拍
  C `nextturn` ＝ 同一局、第一拍 `turn >` 请求回合 ⇒ 结算必然已发生
  D `any`      ＝ 请求之后的**下一拍**快照（不管哪一局，会跨局串味）

另外记录两件事，用来判断「0」是真打空还是取数取不到：
  · post 那一拍**字面上有没有** `skill_point` 这个键（没有＝不能当证据）
  · 同一稽古腿预告（home 腿 + festa 腿）为 0 的手有多少（预告 0 ＝ 板说这一手本来就不给 PT）

只读报文目录，不写任何配置、不产系数。
"""
from __future__ import annotations

import collections
import csv
import os
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

CID = {5101, 5102, 5103, 5104, 5105}
# 四种 post 取法（同一批手、同一个 pre，只换 post 那一拍）
CALIBERS = ("samirun", "same_turn", "nextturn", "any")


def legs_pt(data, cid):
    """板上自报的稽古腿预告合计（home 30 + festa 30），取不到返回 None。"""
    tot, has = 0, False
    for holder in ("home_info", "festa_data_set"):
        for c in ((data.get(holder) or {}).get("command_info_array")) or []:
            if c.get("command_id") != cid:
                continue
            for e in c.get("params_inc_dec_info_array") or []:
                if e.get("target_type") == 30:
                    tot += e.get("value") or 0
                    has = True
    return tot if has else None


def main():
    files = sorted(
        (os.path.join(PS.RAW, n) for n in os.listdir(PS.RAW) if n.endswith(".bin")),
        key=PS.sort_key,
    )
    # 先过一遍，只留「带 chara_info.turn 的快照」和「稽古请求」两类事件
    snaps = []          # (pos, file_idx, run_key, turn, skill_point, pt_present, vital, motivation, 五维和)
    reqs = []           # (file_idx, pre_snapshot, cid, basename)
    last_snap = None
    for i, path in enumerate(files):
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        base = os.path.basename(path)
        if base.endswith("_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            if hit is None:
                continue
            common = d[hit] or {}
            if (PS.CHANNELS[hit] == "cmd" and common.get("command_type") == 1
                    and common.get("command_id") in CID):
                reqs.append((i, last_snap, common.get("command_id"), base,
                             common.get("current_vital")))
            continue
        data = d.get("data") or {}
        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        pt = ci.get("skill_point")
        five = sum(ci.get(k) or 0 for k in ("speed", "stamina", "power", "guts", "wiz"))
        last_snap = (len(snaps), i, (ci.get("single_mode_chara_id"), ci.get("start_time")),
                     ci.get("turn"), pt if isinstance(pt, int) else None,
                     isinstance(pt, int), ci.get("vital"), ci.get("motivation"), five)
        snaps.append(last_snap)

    print(f"报文 {len(files)} ｜带 turn 快照 {len(snaps)} ｜稽古请求 {len(reqs)}")

    # 板上预告（keiko_curve 的 CSV）按请求文件名 join，用来把「0 差分」分成
    # 「板本来就预告 0」和「板预告 > 0 却没到账」两类——只有后者才算打空。
    pred = {}
    csv_path = os.path.join("out", "lv", "_keiko_curve_perhand.csv")
    if os.path.exists(csv_path):
        with open(csv_path, encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                pred[r["file"]] = (r["pt_pred"], r["pt_real"], r.get("lv_10x"))
    print(f"预告 join 命中 {sum(1 for r in reqs if r[3] in pred)}/{len(reqs)} 手"
          f"（CSV {csv_path}）")

    rows = []
    for idx, pre, cid, name, vital in reqs:
        if pre is None:
            continue
        s_pos, s_idx, key, turn, pt0, present, vit0, mot0, five0 = pre
        if not present or pt0 is None:
            continue
        cand = {k: None for k in CALIBERS}
        for j in range(s_pos + 1, len(snaps)):
            sn = snaps[j]
            if sn[1] <= idx:                      # 快照要晚于这条请求
                continue
            if cand["any"] is None:
                cand["any"] = sn
            if sn[2] != key:                      # 其余口径都要求同一局
                continue
            if cand["samirun"] is None:
                cand["samirun"] = sn
            if sn[3] == turn and cand["same_turn"] is None:
                cand["same_turn"] = sn
            if sn[3] is not None and turn is not None and sn[3] > turn:
                cand["nextturn"] = sn
                break
        rows.append((key, turn, cid, pt0, cand, name, vital, vit0, mot0, five0))

    def delta(row, name):
        sn = row[4][name]
        if sn is None or not sn[5]:
            return None
        return sn[4] - row[3]

    def summarize(name, sel=None):
        n = zero = neg = absent = 0
        for row in rows:
            if sel and not sel(row):
                continue
            sn = row[4][name]
            if sn is None:
                continue
            n += 1
            if not sn[5]:
                absent += 1
                continue
            d = sn[4] - row[3]
            if d < 0:
                neg += 1
            elif d == 0:
                zero += 1
        return n, zero, neg, absent

    print()
    print("post 取法        配对手数   PT<=0  (占比)    ==0      <0   post 无 skill_point 键")
    for name in CALIBERS:
        n, zero, neg, absent = summarize(name)
        z = zero + neg
        print(f"  {name:<12}  {n:>7}  {z:>6}({z/n*100 if n else 0:>4.1f}%)  "
              f"{zero:>6}  {neg:>6}  {absent:>6}")

    # turn≥30 段（和 §11.3 那句同一把尺子）
    print()
    print("只取 turn>=30（§11.3 那句的段界）：")
    for name in CALIBERS:
        n, zero, neg, absent = summarize(name, sel=lambda r: (r[1] or 0) >= 30)
        z = zero + neg
        print(f"  {name:<10} {z:>4}/{n:>4} = {z/n*100 if n else 0:.1f}%"
              f"   （==0 {zero} ／ <0 {neg}）")

    # 0 差分拆两类：板本来就预告 0 vs 预告>0 却没到账
    print()
    print("把「预告>0」的手单独拿出来（真·打空只可能出现在这一堆里）：")
    has_pred = [r for r in rows if r[5] in pred and pred[r[5]][0] not in (None, "")]
    _ = has_pred
    print(f"  带预告的手 {len(has_pred)} / {len(rows)}")
    for name in CALIBERS:
        n = zero = 0
        n0 = zero0 = 0
        for row in has_pred:
            d = delta(row, name)
            if d is None:
                continue
            p = int(float(pred[row[5]][0]))
            if p > 0:
                n += 1
                zero += d <= 0
            else:
                n0 += 1
                zero0 += d <= 0
        print(f"  {name:<10} 预告>0：{zero:>3}/{n:<4} = {zero/n*100 if n else 0:>4.1f}%"
              f"   预告==0：{zero0:>3}/{n0:<4}")

    # 交叉核对：CSV 自己的 pt_real 和 samirun 口径应当逐手相同
    both = [(pred[r[5]][1], delta(r, "samirun")) for r in rows
            if r[5] in pred and pred[r[5]][1] not in (None, "") and delta(r, "samirun") is not None]
    diff = [a for a, b in both if int(float(a)) != b]
    print()
    print(f"CSV.pt_real vs 本脚本 samirun：{len(both)} 手可比，不一致 {len(diff)} 手"
          f"{'  ⇒ 同口径' if not diff else '  ⇒ 口径不同，先查这个'} {diff[:6]}")

    # post 相对请求的回合差分布：解释为什么 any 会取到未结算的一拍
    print()
    print("post 快照的 turn 相对请求回合的差（Δturn=0 ⇒ 回合还没推进，结算未必发生）：")
    for name in CALIBERS:
        g = collections.Counter()
        for row in rows:
            sn = row[4][name]
            if sn is None or row[1] is None or sn[3] is None:
                continue
            g[sn[3] - row[1]] += 1
        tot = sum(g.values())
        zero_d = g.get(0, 0)
        print(f"  {name:<10} n={tot:>4} Δturn=0 占 {zero_d} ({zero_d/tot*100 if tot else 0:.1f}%)"
              f" ｜分布 {sorted(g.items())[:6]}")

    shape_table(rows, pred)
    vital_table(rows, pred)


def shape_table(rows, pred):
    """PT<=0 那几手的**完整形状**：失败若有惩罚，应体现在体力/干劲/五维上。"""
    print()
    print("PT<=0 的手逐手形状（引擎「無茶は厳禁！」口径预测：体力 +10／干劲 −1／三轴共 −30）：")
    print("  请求文件                              回合 演目  ΔPT   Δ体力  Δ干劲  Δ五维和")
    for r in rows:
        sn = r[4]["samirun"]
        if sn is None or not sn[5]:
            continue
        d = sn[4] - r[3]
        if d > 0:
            continue
        print(f"  {r[5]:<35} t{r[1]:<4} {r[2]} {d:>+6}"
              f"   {sn[6] - r[7] if isinstance(sn[6], int) else -1:+4}"
              f"    {sn[7] - r[8] if isinstance(sn[7], int) else -1:+4}"
              f"     {sn[8] - r[9]:+6}")


def vital_table(rows, pred):
    """真机稽古手的体力 × 引擎自己的失败公式 —— 判「引擎把稽古手算成会失败」是不是虚高。

    失败公式（上游 `umasim/src/game/traits.rs:550` 原式，阈值取 `data/constants.json`）：
        x0 = training_vital_threshold[train][train_lv-1]，f = (100-vital)(x0-vital)/40 (vital<x0)
    `train_lv` 用 CSV 的 `lv_10x`（= `chara_info.training_level_info_array` 那条轴的训练 Lv），
    只取 friend/advice 之外的裸值 ⇒ 这是**最保守**的一版（教诲 205、`fail_rate_drop`、
    `good_trainer` 只会把 f 再压低），所以「公式预测的失败」是**上界**。
    """
    sys.path.insert(0, ".")
    import json
    with open("data/constants.json", encoding="utf-8") as fh:
        TH = json.load(fh)["training_vital_threshold"]
    X0 = {5101 + i: TH[i] for i in range(5)}

    def lv_of(name):
        try:
            return max(1, min(5, int(float(pred[name][2]))))
        except (KeyError, IndexError, TypeError, ValueError):
            return 1

    print()
    print("真机稽古手按体力分档（X0 ＝ 引擎该位该训练 Lv 的失败阈值）：")
    print("  档位                       手数  PT<=0  实际失败率   公式预测失败数   平均PT/手")
    recs = []
    for r in rows:
        sn = r[4]["samirun"]
        if sn is None or not sn[5] or r[6] is None:
            continue
        v = r[6]
        row = X0.get(r[2], TH[0])
        x0 = row[lv_of(r[5]) - 1]
        f = (100.0 - v) * (x0 - v) / 40.0 if v < x0 else 0.0
        recs.append((r, v, x0, min(max(f, 0.0), 99.0) / 100.0, sn[4] - r[3]))
    for label, keep in (("vital < X0", lambda t: t[1] < t[2]),
                        ("X0 <= vital < X0+20", lambda t: t[2] <= t[1] < t[2] + 20),
                        ("vital >= X0+20", lambda t: t[1] >= t[2] + 20)):
        g = [t for t in recs if keep(t)]
        n = len(g)
        z = sum(1 for t in g if t[4] <= 0)
        exp = sum(t[3] for t in g)
        gains = sorted(t[4] for t in g)
        print(f"  {label:<24} {n:>4}  {z:>5}   {z/n*100 if n else 0:>7.1f}%   {exp:>12.1f}"
              f"      {(sum(gains)/n) if n else 0:>6.1f}")
    n = len(recs)
    z = sum(1 for t in recs if t[4] <= 0)
    exp = sum(t[3] for t in recs)
    print(f"  {'合计':<24} {n:>4}  {z:>5}   {z/n*100 if n else 0:>7.1f}%   {exp:>12.1f}"
          f"   （公式/实际 = {exp/z if z else float('inf'):.1f}×）")
    worst = sorted(recs, key=lambda t: -t[3])[:6]
    print("  公式给的概率最高的几手（看它们实际到账没有）：")
    for r, v, x0, f, d in worst:
        print(f"    t{r[1]:<3} cid{r[2]} vital={v:<4} x0={x0:<5} 公式 {f*100:>5.1f}%  实到 PT {d:+d}")


vital_table_main = vital_table


if __name__ == "__main__":
    main()
