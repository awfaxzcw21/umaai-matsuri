#!/usr/bin/env python3
"""第 5 步：PT 定价口径对照（决策层 pt_favor_rate × 官方 pt_score_rate）。
    cd E:/HJM/umaai-matsuri && python tools/pt_favor_double_count_20261103.py
"""
import json

d = json.load(open("data/constants.json", encoding="utf-8"))
T = d["five_status_final_score"]
RATE = d["pt_score_rate"]                 # 2.0，官方固定
AX = ("速", "耐", "力", "根", "智")
ANCHOR = [1998, 1470, 1656, 1344, 1573]
PT_ANCHOR = 8339


def revise(v):
    return 2 * v - 1200 if v > 1200 else v


look = lambda v: T[min(max(revise(v), 0), len(T) - 1)]
eng = lambda v: T[min(max(v, 0), len(T) - 1)]

S_off = sum(look(v) for v in ANCHOR)
S_eng = sum(eng(v) for v in ANCHOR)
print("官方 pt_score_rate = %.1f PT/点（固定，不可调）" % RATE)
print("锚点 run179：五维分(官方口径)=%d 五维分(引擎错口径)=%d · PT=%d" % (S_off, S_eng, PT_ANCHOR))
print()
print("目标函数里 五维:PT 的分量占比（锚点局）")
print("口径".ljust(34), "五维分", "PT 计价", "PT 分", "五维/PT 比", "离官方比偏")
rows = [
    ("官方计分（revise + favor=1.0）", S_off, RATE),
    ("上游旧出厂（无 revise + favor 2.0）", S_eng, RATE * 2.0),
    ("ga-r2 出厂（无 revise + favor 1.7271）", S_eng, RATE * 1.7271),
    ("假想修正（revise + favor 1.7271）", S_off, RATE * 1.7271),
    ("假想修正（revise + favor 1.0）", S_off, RATE),
]
ref = S_off / (RATE * PT_ANCHOR)
for name, s, pv in rows:
    pt = pv * PT_ANCHOR
    print("%-34s %6d  %.4f/点 %8.0f   %.3f   %.3f×"
          % (name, s, pv, pt, s / pt, (s / pt) / ref))
print()
print("锚点官方 五维:PT 比 = %.3f" % ref)
print("⇒ favor 1.7271 相对 favor 2.0 把 PT 价降 %.1f%%，把 五维/PT 比从 %.3f 抬到 %.3f（官方 %.3f）"
      % (100 * (1 - 1.7271 / 2.0), S_eng / (RATE * 2 * PT_ANCHOR), S_eng / (RATE * 1.7271 * PT_ANCHOR), ref))
gap_full = ref - S_eng / (RATE * 2.0 * PT_ANCHOR)
gap_now = ref - S_eng / (RATE * 1.7271 * PT_ANCHOR)
print("   离官方的绝对缺口：favor2.0 时 %.3f → favor1.7271 时 %.3f ⇒ 只补了 %.1f%% 的混合比缺口"
      % (gap_full, gap_now, 100 * (1 - gap_now / gap_full)))
print("   残余缺口 %.3f vs 官方 %.3f ⇒ 决策层仍把 PT 相对高估 %.2f×"
      % (gap_now, ref, ref / (S_eng / (RATE * 1.7271 * PT_ANCHOR))))
