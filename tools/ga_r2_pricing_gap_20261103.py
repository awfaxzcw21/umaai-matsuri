#!/usr/bin/env python3
"""终局口径差 + 逐轴低估倍数（只用 data/constants.json，不碰仓内文件）。

复用 tools/revise_pricing_gap.py 的口径（revise / look 末档封顶 / ±10 差分），
只是把输入从 MC dump 换成手写向量表。从仓根跑：
    cd E:/HJM/umaai-matsuri && python tools/ga_r2_pricing_gap_20261103.py
"""
import json

T = json.load(open("data/constants.json", encoding="utf-8"))["five_status_final_score"]
N = len(T)
LAST = T[-1]
AXES = ("速", "耐", "力", "根", "智")
CAP2500 = 2500                      # 剧本硬顶 upper_limit_* (single_mode_scenario id=15)
LIMIT_BASE = [3100, 2400, 2200, 2200, 2400]  # data/constants.json five_status_limit_base


def revise(v):
    return 2 * v - 1200 if v > 1200 else v


def look(v):
    return T[min(max(revise(v), 0), N - 1)]


def look_engine(v):
    """引擎错口径：直接拿显示值当表下标（越界同样末档封顶）。"""
    return T[min(max(v, 0), N - 1)]


def mult(v, h=10):
    """真实边际 / 引擎边际，±h 差分，跟 tools/revise_pricing_gap.py 一致。"""
    eng = (look_engine(min(v + h, N - 1)) - look_engine(v)) / h
    tru = (look(v + h) - look(v)) / h
    return eng, tru, tru / eng


def row(name, vec, note=""):
    off = sum(look(v) for v in vec)
    eng = sum(look_engine(v) for v in vec)
    oob = [AXES[i] for i, v in enumerate(vec) if revise(v) >= N]
    print("%-26s vec=%s sum=%d | 官方Σ T[revise]=%d 引擎Σ T[v]=%d 差=%d (%.1f%%) %s"
          % (name, vec, sum(vec), off, eng, off - eng,
             100.0 * (off - eng) / eng if eng else 0.0,
             ("越界轴:" + ",".join(oob) + " ⚠️不可信") if oob else ""))
    if note:
        print(" " * 28 + note)
    return off, eng


print("表长 %d · 末档 T[%d]=%d · revise(3149)=%d 越界=%s"
      % (N, N - 1, LAST, revise(3149), revise(3149) >= N))
print()
print("== 第 2 步：终局口径差 ==")
a = row("run179 锚点真值", [1998, 1470, 1656, 1344, 1573],
        "预期官方格 = 42201（门禁锚点）；越界/上限检查：全部 <= 2500 硬顶")
row("  ↳ 断言 42201", [1998, 1470, 1656, 1344, 1573])
print("   锚点复现零差：%s" % (a[0] == 42201))

# 我们 CRN 基线终局五维：本 HEAD dump out/_gate/crn_B_s[012].jsonl 的逐轴均值
import glob, statistics
rows = [json.loads(l) for f in sorted(glob.glob("out/_gate/crn_B_s[012].jsonl")) for l in open(f, encoding="utf-8")]
lvl = [int(round(statistics.fmean(r["five"][i] for r in rows))) for i in range(5)]
row("CRN 基线(crn_B 逐轴均值)", lvl,
    "一手数字出处：out/_gate/crn_B_s[012].jsonl 逐局 five[] 的均值，%d 局" % len(rows))
# 逐局口径差的均值/分布
deltas = [sum(look(v) for v in r["five"]) - sum(look_engine(v) for v in r["five"]) for r in rows]
offd = [sum(look(v) for v in r["five"]) for r in rows]
engd = [sum(look_engine(v) for v in r["five"]) for r in rows]
print("   逐局：官方均值 %.1f · 引擎口径均值 %.1f · 差均值 %.1f (中位 %.1f, 分位5/95 %.1f/%.1f) · 失败 %d 条"
      % (statistics.fmean(offd), statistics.fmean(engd), statistics.fmean(deltas),
         statistics.median(deltas),
         sorted(deltas)[int(0.05 * len(deltas))], sorted(deltas)[int(0.95 * len(deltas))],
         sum(1 for r, o in zip(rows, offd) if o != r["stat"])))

g = row("ga-r2 档终局水平", [3149, 2274, 2045, 1575, 2348],
        "合法性：速 3149 > 剧本硬顶 2500 且 > five_status_limit_base[0]=3100 ⇒ 我们口径下非法")
print("   ga-r2 逐格：")
for i, v in enumerate([3149, 2274, 2045, 1575, 2348]):
    rv = revise(v)
    print("    %s v=%d revise=%d 越界=%s 官方格=%s 引擎格=%s 超2500=%s 超本轴base=%s"
          % (AXES[i], v, rv, rv >= N, look(v), look_engine(v), v > CAP2500, v > LIMIT_BASE[i]))
# 速夹到 2500 的备用口径
clip = [min(v, CAP2500) for v in (3149, 2274, 2045, 1575, 2348)]
row("ga-r2 夹到硬顶 2500", clip, "备用：速 3149→2500，其余不动")

print()
print("== 第 3 步：逐轴低估倍数（±10 差分，真实边际/引擎边际）==")
print("水平   内部水平  引擎边际/点  真实边际/点  倍数   ｜ 表是单轴通用：同一水平五轴倍数相同")
for L in (1500, 2000, 2500, 2800):
    e, t, m = mult(L)
    print("%-6d %8d %11.2f %12.2f   x%.2f" % (L, revise(L), e, t, m))
print("   （fine grid 附赠：看梯度用）")
for L in (1300, 1500, 1700, 2000, 2200, 2500, 2600, 2800):
    e, t, m = mult(L)
    print("   v=%4d 内部=%4d 引擎=%.2f 真实=%.2f x%.2f%s"
          % (L, revise(L), e, t, m, "  ⚠️越界" if revise(L) >= N else ""))
print()
print("分轴读表（各轴在它自己那个水平上的倍数）：")
print("轴   水平   内部水平  引擎边际  真实边际  倍数   来源")
for src, vecs in (("crn_B(HEAD基线)", lvl),
                  ("ga-r2 终局", [3149, 2274, 2045, 1575, 2348])):
    for i, v in enumerate(vecs):
        e, t, m = mult(min(v, N - 20))
        print("%s %6d %8d %9.2f %9.2f  x%.2f  %s%s"
              % (AXES[i], v, revise(v), e, t, m, src,
                 "  ⚠️revise 越界末档封顶" if revise(v) >= N else ""))
