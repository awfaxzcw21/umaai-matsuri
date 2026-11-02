#!/usr/bin/env python3
"""核对「五维分 = Σ 表[revise(五维)]」并量出估值层不套 revise 的低定价倍数。

用法（读哪份 dump 就用哪个 glob，默认拿 #23 的壳基线；**表在仓根 `data/` 下，必须从仓根跑**）：

    cd E:/HJM/umaai-matsuri
    python tools/revise_pricing_gap.py "E:/HJM/_s7x2/_exp/sw_base_s[012].jsonl"
    python tools/revise_pricing_gap.py            # 不带参数走上面的默认

三件事：
1. 逐局断言 `stat == Σ five_status_final_score[revise(v)]`（revise = 1200 软上限的显示层逆）；
2. 逐轴覆盖率（对锚点 run179 终值）——产出缺口在哪一格；
3. 每显示 1 点的**引擎边际**（不套 revise，即 `value.rs::status_score` 的口径）
   对**真实边际**（套 revise）的倍数——这是定价缺陷的量，不是旋钮该吸收的东西。

口径出处：`docs/旋钮定价判读_20261103.md`。
"""

import glob
import json
import statistics
import sys

TABLE = json.load(open("data/constants.json", encoding="utf-8"))["five_status_final_score"]
AXES = ("速", "耐", "力", "根", "智")
ANCHOR = (1998, 1470, 1656, 1344, 1573)  # run179/cid179 终局真值
DEFAULT_DUMP = "E:/HJM/_s7x2/_exp/sw_base_s[012].jsonl"


def revise(v: int) -> int:
    """显示值 → 查表用的「等效未衰减值」（>1200 段每点存两头）。"""
    return 2 * v - 1200 if v > 1200 else v


def look(v: int) -> int:
    """表查值，越界按末档封顶（表只有 3,802 格）。"""
    return TABLE[min(max(revise(v), 0), len(TABLE) - 1)]


def main() -> int:
    pattern = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DUMP
    files = sorted(glob.glob(pattern))
    if not files:
        raise SystemExit("没有 dump：%s" % pattern)
    rows = [json.loads(line) for f in files for line in open(f, encoding="utf-8")]
    bad = sum(1 for r in rows if sum(look(v) for v in r["five"]) != r["stat"])
    print("%d 局（%d 份 dump）：Σ表[revise(五维)] == 引擎 stat 失败 %d 条" % (len(rows), len(files), bad))

    level = [statistics.fmean(r["five"][i] for r in rows) for i in range(5)]
    print("\n轴   水平    覆盖率   内部水平   引擎边际/点  真实边际/点  低估倍数")
    for i, name in enumerate(AXES):
        v = int(round(level[i]))
        engine = (TABLE[min(v + 10, len(TABLE) - 1)] - TABLE[v]) / 10.0  # 估值层现口径：不套 revise
        truth = (look(v + 10) - look(v)) / 10.0  # 游戏口径：先 revise 再查表
        print("%s %6d   %6.3f   %7d   %9.2f  %10.2f     x%.2f"
              % (name, v, level[i] / ANCHOR[i], revise(v), engine, truth, truth / engine))
    print("\n⚠️ 倍数随水平变（表是折线），不许当常数抄进配置。")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
