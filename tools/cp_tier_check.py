#!/usr/bin/env python3
"""发表会四场：档位边界 + 每轴软上限腿 的逐样本验算（#16 的一手依据）。

对每个携带 `215xxx` 的报文窗口，用 `pre = post − d_stats` 还原结算前的五维，回答两问：

  A. 档位（`check_point_result_array.result_type` 1=成功 / 2=大成功）到底由什么决定？
     ⇒ 看 `min(全演目 Lv)` 与 `single_mode_15_schedule.need_act_level` 的边界。
  B. 名义「全能力 +N」实到几档？与 `pre ≥ 1200` 的关系，按**场次 × 档**交叉。

只落观测、不落结论：任何一行判读都要看 n 和例外数，别拿均值。

从仓根跑：`python tools/cp_tier_check.py`
输入：`E:/HJM/_pttrace/packet_gains.csv`（`scan_ledger.py` 产出的逐报文差分表；
      可用环境变量 `CP_CSV` 覆盖）+ `E:/HJM/jidianbeiAI0.22/out/hachimi_raw/*.bin`（一手 msgpack 报文）。
"""
from __future__ import annotations

import collections
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402  复用同一套 get_ci / RAW 口径

RAW = PS.RAW
CSV_PATH = os.environ.get("CP_CSV", "E:/HJM/_pttrace/packet_gains.csv")
AX = ("speed", "stamina", "power", "guts", "wiz")

# event_id → (场次, 档)。215012 同 id 两档，靠 d_pt 分（300=大 / 150=成）；215013 全 corpus 未观测。
EID = {215010: (1, 2), 215011: (1, 1), 215012: (2, 2), 215013: (2, 1),
       215014: (3, 2), 215015: (3, 1), 215027: (4, 2), 215028: (4, 1)}
# 名义全能力奖励（报文众数；B 问要验的就是它）
NOMINAL = {(1, 2): 5, (1, 1): 3, (2, 2): 10, (2, 1): 5,
           (3, 2): 15, (3, 1): 10, (4, 2): 15, (4, 1): 15}
# 第 1~3 场 = master.mdb `single_mode_15_schedule`.need_act_level；第 4 场表内无行
NEED_TBL = {1: 3, 2: 5, 3: 6, 4: None}
SOFT_CAP = 1200  # `chara_info.default_max_*` 各局恒 1200


def compress(pre, nom):
    """引擎 `soft_cap_gain` 的同款公式：超出 1200 的那一段减半（各自向下取整）。"""
    below = min(nom, max(0, SOFT_CAP - pre))
    return below + (nom - below) // 2


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit("找不到 %s（可用 CP_CSV 覆盖）—— 先跑 scan_ledger.py" % CSV_PATH)
    rows = [r for r in csv.DictReader(open(CSV_PATH, encoding="utf-8"))
            if r["req_event_id"] and int(r["req_event_id"]) in EID]
    rows.sort(key=lambda r: (r["run"], int(r["req_turn"])))

    tier_ok = tier_bad = 0
    mins = collections.defaultdict(lambda: collections.defaultdict(list))
    # (场次, 档) → 每轴实得落在哪种压缩形态；「其它」= 两个候选值都对不上（多半撞 max_* 硬顶）
    leg = collections.defaultdict(lambda: collections.Counter())
    # (场次, 档) → {d_pt: n}：PT 腿有没有方差，看这张表
    pt = collections.defaultdict(collections.Counter)
    others = []

    for r in rows:
        path = os.path.join(RAW, r["pkt_file"])
        if not os.path.exists(path):
            continue
        data = ml.decode_file(path)
        data = data.get("data", data)
        ci = PS.get_ci(data) or {}
        fs = data.get("festa_data_set") or {}
        lv = [a.get("level") for a in (fs.get("act_info_array") or [])]
        rt = {x.get("schedule_id"): x.get("result_type")
              for x in (fs.get("check_point_result_array") or [])}

        idx, tier = EID[int(r["req_event_id"])]
        if idx == 2 and int(r["d_pt"]) != 300:
            tier = 1
        pt[(idx, tier)][r["d_pt"]] += 1
        d = [int(x) for x in r["d_stats"].split("|")]
        post = [ci[k] for k in AX]
        pre = [p - x for p, x in zip(post, d)]
        nom = NOMINAL[(idx, tier)]

        for i in range(5):
            exp = compress(pre[i], nom)
            if pre[i] >= SOFT_CAP:                    # 整段都在线上
                key = "线上压缩" if d[i] == exp else ("线上未压缩" if d[i] == nom else "其它")
            elif pre[i] + nom > SOFT_CAP:             # 跨线：只压超出那一段
                key = "跨线压缩" if d[i] == exp else ("跨线未压" if d[i] == nom else "其它")
            else:                                     # 整段在线下
                key = "线下满额" if d[i] == nom else "其它"
            leg[(idx, tier)][key] += 1
            if key == "其它":
                others.append("run%-6s t%-3s #%d档%d pre=%d d=%d 名义=%d 压缩应得=%d max_*=%s"
                              % (r["run"][:6], r["req_turn"], idx, tier, pre[i], d[i], nom, exp,
                                 ci.get("max_" + AX[i])))

        mn = min(lv) if lv else None
        if mn is not None:
            mins[idx][tier].append(mn)
        need = NEED_TBL[idx]
        verdict = "第4场表内无 need ⇒ 只记 min=%s" % mn
        if need is not None and mn is not None:
            pred = 2 if mn >= need else 1
            ok = pred == tier
            tier_ok += ok
            tier_bad += not ok
            verdict = "min>=%d⇒大 %s" % (need, "OK" if ok else "✗ 预测档%d" % pred)
        print("run%-6s t%-3s #%d 档%d d_pt=%-4s minLv=%-3s result_array#%d=%-3s maxActLv=%-3s lv=%s  %s"
              % (r["run"][:6], r["req_turn"], idx, tier, r["d_pt"], mn, idx,
                 rt.get(idx, "-"), (fs.get("festa_info") or {}).get("max_act_level"),
                 ",".join(map(str, lv)), verdict))

    print("\n== A. 档位边界：档 = (min(全演目 Lv) >= need) ==")
    for idx in sorted(mins):
        for tier in sorted(mins[idx]):
            v = mins[idx][tier]
            print("  #%d 档%d n=%d  minLv ∈ [%s, %s]  Lv=%s"
                  % (idx, tier, len(v), min(v), max(v), sorted(set(v))))
    print("  ⇒ 边界一致：%d 对 / %d 错" % (tier_ok, tier_bad))

    print("\n== B. 每轴实得的压缩形态（按场次 × 档交叉；「线上」= pre ≥ 1200）==")
    cols = ("线下满额", "跨线压缩", "跨线未压", "线上压缩", "线上未压缩", "其它")
    print("  场次 档  " + " ".join("%6s" % c for c in cols))
    for k in sorted(leg):
        c = leg[k]
        print("  #%d   档%d  " % (k[0], k[1]) + " ".join("%6d" % c[x] for x in cols))
    if others:
        print("\n  「其它」样本（名义值与压缩应得值都对不上 ⇒ 多半撞 max_* 硬顶）：")
        for line in others:
            print("   ", line)

    print("\n== C. PT 腿取值分布（有没有方差看这张表）==")
    for k in sorted(pt):
        v = pt[k]
        tag = "单值" if len(v) == 1 else "有方差"
        print("  #%d 档%d n=%d %s  %s"
              % (k[0], k[1], sum(v.values()), tag,
                 " / ".join("%s×%d" % (d, n) for d, n in sorted(v.items(), key=lambda x: int(x[0])))))
    tot = sum(sum(v.values()) for v in pt.values())
    great = sum(sum(v.values()) for k, v in pt.items() if k[1] == 2)
    print("  合计 %d 个样本（大成功 %d / 成功 %d）" % (tot, great, tot - great))


if __name__ == "__main__":
    main()
