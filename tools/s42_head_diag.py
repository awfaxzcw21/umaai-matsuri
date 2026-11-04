#!/usr/bin/env python3
"""#42 剂量响应诊断：加头臂的「五维倒 U」是不是溢出/偏科造成的。

配对单位 = 种子分片（同 `paired_board_ci.py`）；只算盘上已有的量，不产常数。

    python tools/s42_head_diag.py
"""
import glob
import json
import math
import os
import re

SOFT_CAP = 1200.0  # docs/每回合收益表：超 1200 的增量减半（有报文依据，非旋钮）
T975 = 1.960
ARMS = [("boost0", "arm0_boost0"), ("boost150", "arm1_boost150"),
        ("boost500", "arm3_boost500"), ("head-first", "arm2_headfirst")]
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# 主仓这份是归档件：证据分片在一次性壳 `_wt_hg/out/s42/` 里，壳删了就要用 `S42_OUT` 指过去
OUT = os.environ.get("S42_OUT") or os.path.join(ROOT, "out", "s42")


def per_game(pat):
    """每局一行：五维和 / 五维分 / PT / 溢出质量 / 偏科跨度 / 每轴均值。"""
    rows = []
    for p in sorted(glob.glob(pat)):
        shard = int(re.search(r"_s(\d+)\.jsonl$", os.path.basename(p)).group(1))
        with open(p, encoding="utf-8") as f:
            for line in f:
                g = json.loads(line)
                fv = g["five"]
                rows.append({
                    "shard": shard,
                    "sum": sum(fv),
                    "stat": g["stat"],
                    "pt": g["pt"],
                    "over": sum(max(0.0, v - SOFT_CAP) for v in fv),
                    "capped_axes": sum(1 for v in fv if v > SOFT_CAP),
                    "spread": max(fv) - min(fv),
                    "five": fv,
                })
    return rows


def by_shard(rows):
    d = {}
    for r in rows:
        d.setdefault(r["shard"], []).append(r)
    return {s: {k: sum(x[k] for x in v) / len(v) for k in
                ("sum", "stat", "pt", "over", "capped_axes", "spread")} for s, v in d.items()}


def axis_mean(rows):
    n = len(rows)
    return [sum(r["five"][a] for r in rows) / n for a in range(5)]


def diff_ci(pa, pb, field):
    keys = sorted(set(pa) & set(pb))
    d = [(pb[k][field] - pa[k][field]) for k in keys]
    n = len(d)
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    half = T975 * sd / math.sqrt(n)
    return m, m - half, m + half, n


def main():
    data = {}
    for label, tag in ARMS:
        rows = per_game(os.path.join(OUT, "dump_%s_s*.jsonl" % tag))
        data[label] = (rows, by_shard(rows))
        print("%-11s 局数 %4d  五维和 %7.1f  五维分 %8.1f  PT %7.1f  溢出>1200 %6.1f  "
              "超1200轴数 %4.2f  偏科跨度 %6.1f" % (
                  label, len(rows),
                  sum(r["sum"] for r in rows) / len(rows),
                  sum(r["stat"] for r in rows) / len(rows),
                  sum(r["pt"] for r in rows) / len(rows),
                  sum(r["over"] for r in rows) / len(rows),
                  sum(r["capped_axes"] for r in rows) / len(rows),
                  sum(r["spread"] for r in rows) / len(rows)))
        print("           逐轴五维 " + " ".join("%7.1f" % v for v in axis_mean(rows)))
    base = data["boost0"][1]
    print("\n配对差（分片=配对单位，n=17，vs boost0）")
    print("%-11s %18s %18s %18s %18s" % ("臂", "五维和", "五维分", "PT", "溢出质量>1200"))
    for label, _ in ARMS[1:]:
        pb = data[label][1]
        cells = []
        for f in ("sum", "stat", "pt", "over"):
            m, lo, hi, n = diff_ci(base, pb, f)
            cells.append("%+8.1f [%+7.1f,%+7.1f]" % (m, lo, hi))
        print("%-11s %s" % (label, " ".join(c.rjust(22) for c in cells)))
    with open(os.path.join(OUT, "head_diag.json"), "w", encoding="utf-8") as f:
        json.dump({label: {"axis_mean": axis_mean(data[label][0]),
                           "pooled": {k: sum(r[k] for r in data[label][0]) / len(data[label][0])
                                      for k in ("sum", "stat", "pt", "over", "capped_axes", "spread")}}
                   for label, _tag in ARMS}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
