#!/usr/bin/env python3
"""配对剂量读数：两臂只差**一个输入字段**时，直接量「这一档剂量换回多少分」。

和 `crn_gate.py` 的分工：判官算的是 `e_i = |a_i − 真值| − |b_i − 真值|`（离锚点更近幅度），
两臂同侧时会退化成产出计；剂量实验要的是 **原始差 B−A 的 95% t 区间**（这一格值多少分），
判官不给这个量，所以另开一支。两边都跑，别只报一边。

    python tools/paired_dose.py \
        --a 'E:/HJM/_keileg_s59/out/s59_H_s*.jsonl' --a-label cap10 \
        --b 'E:/HJM/_keileg_s59/out/s59_L_s*.jsonl' --b-label cap6

配对判据 = (片号, 样本号 i)。片号从文件名尾部 `_s<N>.jsonl` 取，同片同种子 ⇒ 随机流对齐，
差的只有那个被剂的字段；MC_N 不同会静默少配，脚本把配不上的条数打出来。
只读盘上 dump，不跑引擎、不动 data/。
"""

import argparse
import glob
import json
import math
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from forced_cp_ci import t975  # noqa: E402  本仓统一 t 值表

LINES = [("LG", 76000), ("LG15", 85600), ("LG24", 90900), ("LF", 91400)]
# dump 里逐马一行；这些字段是引擎口径的原生列
FIELDS = ("real", "sum", "stat", "pt", "grade", "native")
SEED_RE = re.compile(r"_s(\d+)\.jsonl$")


def load(pattern, tag):
    """展开 glob 读成 {(seed, i): row}。文件名不带 _sN 的直接拒绝，不静默当成同一种子。"""
    out = {}
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit("找不到 dump：%s" % pattern)
    for p in files:
        m = SEED_RE.search(os.path.basename(p).replace(".jsonl", "") + ".jsonl")
        if not m:
            raise SystemExit("臂 %s 的文件名没有 _s<seed> 后缀，无法配对：%s" % (tag, p))
        seed = int(m.group(1))
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                key = (seed, int(r["i"]))
                if key in out:
                    raise SystemExit("臂 %s 键重复 %r（同种子重复落盘？）%s" % (tag, key, p))
                out[key] = r
    return out


def ci(diffs):
    """配对差的均值 + 95% t 区间 + 符号数（正/负/零）。"""
    n = len(diffs)
    if n < 2:
        return (float("nan"), float("nan"), float("nan"), n, 0, 0, 0)
    m = sum(diffs) / n
    s = math.sqrt(sum((x - m) ** 2 for x in diffs) / (n - 1))
    t = t975(n - 1)
    half = t * s / math.sqrt(n)
    pos = sum(1 for x in diffs if x > 0)
    neg = sum(1 for x in diffs if x < 0)
    return (m, m - half, m + half, n, pos, neg, n - pos - neg)


def N(v):
    return "{:,.0f}".format(v)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--a", required=True, help="臂 A 的 dump glob（剂量高的一侧）")
    ap.add_argument("--b", required=True, help="臂 B 的 dump glob（剂量低的一侧）")
    ap.add_argument("--a-label", default="A")
    ap.add_argument("--b-label", default="B")
    args = ap.parse_args(argv)

    A, B = load(args.a, args.a_label), load(args.b, args.b_label)
    keys = sorted(set(A) & set(B))
    print("臂 A=%s n=%d ｜ 臂 B=%s n=%d ｜ 配上 %d（A 单边 %d／B 单边 %d）"
          % (args.a_label, len(A), args.b_label, len(B),
             len(keys), len(A) - len(keys), len(B) - len(keys)))
    if not keys:
        raise SystemExit("一条都没配上：检查两臂的片号与 MC_N 是否一致")
    seeds = sorted({k[0] for k in keys})
    print("配对片号 %s ｜ 每片配上限 %d 条" % (seeds, max(sum(1 for k in keys if k[0] == s) for s in seeds)))

    print("\n【1 两臂各自的量（引擎口径，非真值）】")
    print("  %-8s %6s %9s %9s %9s" % ("组", "n", "real med", "五维和 med", "pt med"))
    for tag, D in ((args.a_label, A), (args.b_label, B)):
        rows = [D[k] for k in keys if k in D]
        print("  %-8s %6d %9s %9s %9s"
              % (tag, len(rows), N(st.median([r["real"] for r in rows])),
                 N(st.median([r["sum"] for r in rows])), N(st.median([r["pt"] for r in rows]))))

    print("\n【2 配对差 A−B（95%% t 区间，n=%d）】" % len(keys))
    print("  %-10s %10s %18s %8s  %s" % ("量", "均值", "95%区间", "跨0?", "符号 +/−/0"))
    for name in FIELDS:
        d = ci([A[k][name] - B[k][name] for k in keys])
        m, lo, hi, n, pos, neg, zer = d
        print("  %-10s %10s [%9s, %9s] %8s  %d/%d/%d"
              % (name, N(m), N(lo), N(hi), "是" if lo <= 0 <= hi else "否", pos, neg, zer))
    # 演目 Lv 末值：剂量字段是否真的传到了终局
    d = ci([sum(A[k]["act"]) - sum(B[k]["act"]) for k in keys])
    print("  %-10s %10s [%9s, %9s] %8s  %d/%d/%d"
          % ("act 和", N(d[0]), N(d[1]), N(d[2]), "是" if d[1] <= 0 <= d[2] else "否", d[4], d[5], d[6]))

    print("\n【3 剂量是否生效：演目 Lv 末值分布】")
    for tag, D in ((args.a_label, A), (args.b_label, B)):
        lv = [v for k in keys for v in D[k]["act"]]
        mx = [max(D[k]["act"]) for k in keys]
        print("  %-8s 逐格 max %s ｜ 逐局最高 Lv 的 med %s ｜ 触到全臂最大 Lv(%d) 的格子 %d/%d"
              % (tag, max(lv), N(st.median(mx)), max(lv),
                 sum(1 for v in lv if v == max(lv)), len(lv)))

    print("\n【4 越线率（引擎原生 real 列，档位线见 docs/LF靶子_20261103.md）】")
    for name, line in LINES:
        pa = sum(1 for k in keys if A[k]["real"] >= line)
        pb = sum(1 for k in keys if B[k]["real"] >= line)
        print("  P(≥%-5s %7s) A %3d/%d = %5.1f%% ｜ B %3d/%d = %5.1f%%"
              % (name, N(line), pa, len(keys), 100.0 * pa / len(keys),
                 pb, len(keys), 100.0 * pb / len(keys)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
