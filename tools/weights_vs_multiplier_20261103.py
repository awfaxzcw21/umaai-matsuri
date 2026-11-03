#!/usr/bin/env python3
"""第 4 步：status_weights 与「需要的补偿倍数」对照（纯表算，方向性判读）。
    cd E:/HJM/umaai-matsuri && python tools/weights_vs_multiplier_20261103.py
"""
import json

T = json.load(open("data/constants.json", encoding="utf-8"))["five_status_final_score"]
N = len(T)
AX = ("速", "耐", "力", "根", "智")
W = [0.6465, 0.9881, 0.8387, 0.30, 2.6217]      # ga-r2 出厂 status_weights


def revise(v):
    return 2 * v - 1200 if v > 1200 else v


def look(v):
    return T[min(max(revise(v), 0), N - 1)]


def eng(v):
    return T[min(max(v, 0), N - 1)]


def mult(v, h=10):
    e = (eng(min(v + h, N - 1)) - eng(v)) / h
    t = (look(v + h) - look(v)) / h
    return (t / e) if e else float("nan")


def rank(xs):
    s = sorted(range(len(xs)), key=lambda i: -xs[i])
    r = [0] * len(xs)
    for k, i in enumerate(s):
        r[i] = k + 1
    return r


def spearman(a, b):
    ra, rb = rank(a), rank(b)
    n = len(a)
    return 1 - 6 * sum((x - y) ** 2 for x, y in zip(ra, rb)) / (n * (n * n - 1))


def fit_r2(m, w):
    k = sum(a * b for a, b in zip(m, w)) / sum(a * a for a in m)
    pred = [k * a for a in m]
    mw = sum(w) / len(w)
    sst = sum((b - mw) ** 2 for b in w)
    ssr = sum((b - p) ** 2 for b, p in zip(w, pred))
    return k, pred, 1 - ssr / sst


print("表饱和检查：T[3800]=%d T[3801]=%d ⇒ 显示值 2500 起真实边际=0（官方分在硬顶处封顶）"
      % (T[3800], T[3801]))
print("倍数函数（表对五轴通用，只随水平变）：v=1300 x%.2f · 1500 x%.2f · 1800 x%.2f · 2000 x%.2f"
      % tuple(mult(v) for v in (1300, 1500, 1800, 2000)))
print()

cases = [
    ("crn_B HEAD 基线终局", [1884, 1427, 1476, 1319, 1379]),
    ("ga-r2 档终局（速越界）", [3149, 2274, 2045, 1575, 2348]),
]
for name, vec in cases:
    print("== %s ==" % name)
    print("轴  水平  内部  倍数   权重  倍数名次 权重名次")
    ms, ws, keep = [], [], []
    for i, v in enumerate(vec):
        m = mult(min(v, N - 20))
        oob = revise(v) >= N
        print("%s %5d %5d x%.2f %7.4f %s" % (AX[i], v, revise(v), m, W[i],
              "  ⚠️越界(末档封顶，倍数无意义)" if oob else ""))
        if not oob and m == m:
            ms.append(m); ws.append(W[i]); keep.append(AX[i])
    print("   只用可信轴 %s：倍数极差 %.2f~%.2f (比值 %.3f×)，权重极差 %.2f~%.2f (比值 %.2f×)"
          % ("/".join(keep), min(ms), max(ms), max(ms) / min(ms),
             min(ws), max(ws), max(ws) / min(ws)))
    print("   Spearman(倍数, 权重) = %+.3f" % spearman(ms, ws))
    k, pred, r2 = fit_r2(ms, ws)
    print("   w≈k·倍数 最小二乘 k=%.3f ⇒ 预测权重 %s · R²=%.4f ⇒ 倍数只解释权重方差 %.1f%%"
          % (k, ["%.3f" % p for p in pred], r2, 100 * max(r2, 0)))
    # 智 vs 根 的绝对缺口里能被倍数解释多少
    if "智" in keep and "根" in keep:
        i2, i0 = keep.index("智"), keep.index("根")
        iz, gen = AX.index(keep[i2]), AX.index(keep[i0])
        dm = ms[i2] - ms[i0]
        dw = W[iz] - W[gen]
        print("   智−根：倍数差 %+.3f（占权重差 %.3f 的 %.1f%%）" % (dm, dw, 100 * dm / dw))
    print()

# 五轴全用（含越界轴，仅列示不作结论）
m5 = [mult(min(v, N - 20)) for v in cases[0][1]]
print("== HEAD 基线五轴全入表 ==")
print("   Spearman = %+.3f · fit R² = %.4f" % (spearman(m5, W), fit_r2(m5, W)[2]))
print("   倍数最低的两轴：%s" % [AX[i] for i in sorted(range(5), key=lambda i: m5[i])[:2]])
print("   权重最高的轴：%s（倍数 x%.2f，排第 %d）"
      % (AX[W.index(max(W))], m5[W.index(max(W))], rank(m5)[W.index(max(W))]))
