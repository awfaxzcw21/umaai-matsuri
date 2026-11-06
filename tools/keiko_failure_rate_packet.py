#!/usr/bin/env python3
"""稽古手的**客户端自报 failure_rate**（报文里 `home_info.command_info_array[cid].failure_rate`）
对着引擎的失败公式打——这是比 §13.2 更强的一手：不用反推，板子上直接写着。

背景：#88 用「282 手实际到账」反证引擎的失败模型（4 手 PT≤0、四个差分全 0、6 手 vital≤5 全额到账）。
本轮发现预告面本身就带 `failure_rate` 字段（见 `docs/PT缺口逐源_20261106.md` §16.6 的样例），
于是可以直接问：**客户端在那一手认为失败率是多少**。

做法：按 mtime 顺序重放报文（与 `keiko_curve.py` 同一套配对），
每条稽古请求（`command_type==1`、`command_id∈5101..5105`）取**请求前最后一拍**的
`home_info.command_info_array[cid]`，读它的 `failure_rate / level / training_partner_array`，
再用同一拍 `chara_info` 的 vital／该轴训练 Lv 套引擎公式
`f=(100−vital)(x0−vital)/40`，`x0` 分别按 ①`training_vital_threshold[10x][训练Lv−1]`（引擎现行）
和 ②`single_mode_training` 里 510x 那 50 行（同轴恒定 536/523/532/548/324）算两版。

输出三件事：客户端自报失败率的分布 / 与实到是否到账的一致性 / 引擎两版公式的偏差。
只读报文与只读 mdb，不写 `data/`、不产系数。
"""
from __future__ import annotations

import collections
import json
import os
import sqlite3
import statistics as st
import sys

sys.path.insert(0, "E:/HJM/jidianbeiAI0.22")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msgpack_lite as ml  # noqa: E402
import pt_ledger_scan as PS  # noqa: E402

MDB = "E:/HJM/_ref01/matsuri-ref-01/data/master.mdb"
FESTA = {5101: "速", 5102: "耐", 5103: "力", 5104: "根", 5105: "智"}
AXES = ("speed", "stamina", "power", "guts", "wiz")


def thresholds():
    """两张阈值表：① constants.json（引擎在用）② mdb 的 510x 行（一手出处，引擎没读）。"""
    with open("data/constants.json", encoding="utf-8") as fh:
        eng = json.load(fh)["training_vital_threshold"]
    db = sqlite3.connect(f"file:{MDB}?mode=ro", uri=True)
    x510 = {}
    for cid, fr in db.execute(
        "select command_id, failure_rate from single_mode_training "
        "where command_id between 5101 and 5105 group by command_id"
    ):
        x510[cid] = fr / 10.0
    db.close()
    return eng, x510


def f_rate(vital, x0):
    if vital >= x0:
        return 0.0
    return min(max((100.0 - vital) * (x0 - vital) / 40.0, 0.0), 99.0)


def main():
    eng_th, x510 = thresholds()
    print("510x 一手阈值（mdb）:", {c: v for c, v in sorted(x510.items())})
    print("引擎 10x 阈值（constants.json）:",
          {i: eng_th[i][:5] for i in range(5)})

    files = sorted((os.path.join(PS.RAW, n) for n in os.listdir(PS.RAW) if n.endswith(".bin")),
                   key=PS.sort_key)
    last = None
    pending = None
    rows = []
    for path in files:
        name = os.path.basename(path)
        try:
            d = ml.decode_file(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        if name.endswith("_request.bin"):
            hit = next((k for k in d if k in PS.CHANNELS), None)
            if hit is None:
                continue
            common = d[hit] or {}
            cid = common.get("command_id")
            if (PS.CHANNELS[hit] == "cmd" and common.get("command_type") == 1
                    and cid in FESTA and last is not None):
                cmd = next((c for c in ((last["home"] or {}).get("command_info_array") or [])
                            if c.get("command_id") == cid), None)
                pending = {
                    "name": name, "key": last["key"], "turn": last["turn"], "cid": cid,
                    "vital": last["vital"], "motivation": last["motivation"],
                    "train_lv": (cmd or {}).get("level"),
                    "client_fr": (cmd or {}).get("failure_rate"),
                    "partners": (cmd or {}).get("training_partner_array"),
                    "pt_before": last["pt"],
                }
            else:
                pending = None
            continue
        data = d.get("data") or {}
        ci = PS.get_ci(data)
        if not isinstance(ci, dict) or ci.get("turn") is None:
            continue
        snap = {
            "key": (ci.get("single_mode_chara_id"), ci.get("start_time")),
            "turn": ci.get("turn"), "pt": ci.get("skill_point"),
            "vital": ci.get("vital"), "motivation": ci.get("motivation"),
            "home": data.get("home_info"),
        }
        if pending is not None and snap["key"] == pending["key"]:
            pending["pt_after"] = snap["pt"]
            rows.append(pending)
            pending = None
        last = snap

    print(f"\n配对成功 {len(rows)} 手稽古")
    n_fr = collections.Counter()
    for r in rows:
        n_fr["缺字段" if r["client_fr"] is None else ("=0" if r["client_fr"] == 0 else f">0")] += 1
    print("客户端自报 failure_rate 分布:", dict(n_fr))
    nz = [r for r in rows if r["client_fr"] not in (None, 0)]
    if nz:
        print(f"  非零的 {len(nz)} 手：t" +
              ", ".join(f"{r['turn']}:{r['client_fr']}" for r in nz[:12]))
        z = sum(1 for r in nz if isinstance(r.get("pt_after"), int)
                and r["pt_after"] - r["pt_before"] <= 0)
        print(f"  其中实到 PT≤0：{z}/{len(nz)}")
    zero = [r for r in rows if r["client_fr"] == 0]
    if zero:
        z = sum(1 for r in zero if isinstance(r.get("pt_after"), int)
                and r["pt_after"] - r["pt_before"] <= 0)
        print(f"  自报 0 的手里实到 PT≤0：{z}/{len(zero)}")

    # 引擎两版公式 vs 客户端自报
    print("\n引擎公式与客户端自报的对比（单位：百分点；客户端字段量纲按 ×10 猜测，两种都给）：")
    for tag, f in (("①引擎现行(10x表按训练Lv)", lambda r: f_rate(
            r["vital"], eng_th[r["cid"] - 5101][max(1, min(5, r["train_lv"] or 1)) - 1])),
            ("②510x 一手阈值(同轴恒定)", lambda r: f_rate(r["vital"], x510.get(r["cid"], 53.6)))):
        vals = [f(r) for r in rows if isinstance(r["vital"], int)]
        print(f"  {tag}: 中位 {st.median(vals):.1f}  p90 {st.quantiles(vals, n=10)[-2]:.1f}  "
              f"最大 {max(vals):.1f}  公式给 >0 的手数 {sum(1 for v in vals if v > 0)}/{len(vals)}")
    cf = [r["client_fr"] for r in rows if isinstance(r["client_fr"], int)]
    if cf:
        print(f"  客户端字段：中位 {st.median(cf)}  最大 {max(cf)}  >0 的手数 "
              f"{sum(1 for v in cf if v > 0)}/{len(cf)}")
    print("\n注：客户端那一格是**板子上显示的失败率**，"
          "它是机制的直接读数，比从到账反推强；本脚本不做任何拟合。")


if __name__ == "__main__":
    main()
