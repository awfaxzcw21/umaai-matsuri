#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#legcell：引擎稽古 home/festa 分腿 × (演目 Lv, 人头) 二维格子表

输入：`out/legcell/res_LC_s*.json`（5 片 × 30 局 = 150 局，`matsuri-dist` 插桩壳的输出）
      里的 `keiko_cells` 字段。
输出：与真机侧 `out/lv/_keiko_curve_perhand.csv` **同格子**的对照表 + 两条判据裁决 + 闭合自检。

真机侧的分腿数字是**已量好的**（下面的 `REAL`），这里只并排打印、不重算。
口径对齐：人头 = 该训练位参与的**支援卡张数**（真机 `n_deck`）；大祭成员走 `n_member` 另一列，不数进来。

JSON 里每格给的是「该格成功手的**腿值均值**」+「该格全手/失败手」，所以聚合时
先由 `hands_per_run - failed_per_run` 还原成功手再乘均值求和，**不许**直接平均均值。
"""
import glob
import json
import os
import sys

# 真机分腿读数（out/lv/_keiko_curve_perhand.csv，turn>=30，195 手）：(人头, Lv带) → (n, home, festa)
REAL = {
    (1, "2-5"): (45, 37.9, 27.8),
    (1, "6-8"): (41, 41.2, 65.4),
    (1, "9-10"): (6, 53.0, 162.2),
    (2, "2-5"): (22, 55.6, 39.0),
    (2, "6-8"): (45, 67.8, 101.3),
    (2, "9-10"): (16, 74.2, 204.8),
}
REAL_HEADS_MEAN = 1.32
KEIKO_PT_PER_RUN = 1652.7  # #80 的稽古 PT/局（闭合靶）
EXTRA_KEIKO_PT = 20  # 追加の稽古上段那一份 PT，不在两条腿里
LV_BANDS = [("2-5", (2, 3, 4, 5)), ("6-8", (6, 7, 8)), ("9-10", (9, 10))]


def band_of(lv):
    for name, lvs in LV_BANDS:
        if lv in lvs:
            return name
    return None


def head_axis(h):
    return h if h <= 2 else 3  # 3 = 人头 >=3


def load(paths):
    n = 0
    grid = {}
    dropped = 0
    heads_act = [0.0, 0.0]  # [人头加权合计, 全手合计]（第二条口径，只取均值做核对）
    for p in paths:
        with open(p, encoding="utf-8") as f:
            r = json.load(f)
        nn = r["n"]
        n += nn
        dropped += int(r.get("keiko_sim_dropped_hands", 0))
        for c in (r.get("keiko_cells_act") or {}).get("cells", []):
            heads_act[0] += c["heads"] * c["hands"]
            heads_act[1] += c["hands"]
        for c in r["keiko_cells"]["cells"]:
            ok = (c["hands_per_run"] - c["failed_per_run"]) * nn
            g = grid.setdefault((c["lv"], c["heads"]), [0.0] * 6)
            g[0] += c["hands_per_run"] * nn
            g[1] += ok
            g[2] += c["home_leg_pt"] * ok
            g[3] += c["festa_leg_pt"] * ok
            g[4] += c["extra_keiko"]
    return n, grid, dropped, (heads_act[0] / heads_act[1] if heads_act[1] else 0.0)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "legcell")
    paths = [q for q in sorted(glob.glob(os.path.join(out_dir, "res_LC_s*.json")))
             if ".prefix." not in os.path.basename(q)]
    if not paths:
        print("没有 res_LC_s*.json", file=sys.stderr)
        return 1
    n, grid, dropped, heads_act_mean = load(paths)

    agg = {}
    for (lv, hd), v in grid.items():
        b = band_of(lv)
        if b is None:
            continue
        a = agg.setdefault((head_axis(hd), b), [0.0] * 4)
        for i in range(4):
            a[i] += v[i]

    print(f"# #legcell 引擎稽古分腿格子表 · {n} 局（MC_POLICY=search MC_K=64，锚点 _state_t30_cap10.json）")
    print(f"# 搜索预演假手 {dropped:,} 手（{dropped/max(n,1):,.0f}/局）已被 pause 屏蔽，不进表")
    print()
    print(f"{'人头':>4} {'Lv带':>6} | {'手数/局':>8} {'成功手/局':>9} {'home PT/手':>11} "
          f"{'festa PT/手':>11} {'festa占比':>9} | 真机 home/festa/占比")
    print("-" * 100)
    for hd in (0, 1, 2, 3):
        for b, _ in LV_BANDS:
            a = agg.get((hd, b))
            if not a or a[1] <= 0:
                continue
            hp, ok, home, festa = a[0] / n, a[1], a[2] / a[1], a[3] / a[1]
            ref = REAL.get((hd, b)) if hd in (1, 2) else None
            refs = (f"{ref[1]:.1f}/{ref[2]:.1f}/{ref[2]/(ref[1]+ref[2]):.0%}" if ref else "—")
            print(f"{hd:>4} {b:>6} | {hp:>8.2f} {ok/n:>9.2f} {home:>11.1f} {festa:>11.1f} "
                  f"{festa/(home+festa):>9.0%} | {refs}")

    # 细带对照（和 `out/legcell/real_legs_cells.json` 同一套带，直接对数）
    FINE = [("1-3", (1, 2, 3)), ("4-5", (4, 5)), ("6-8", (6, 7, 8)), ("9-10", (9, 10))]
    real_fine = {}
    rf_path = os.path.join(out_dir, "real_legs_cells.json")
    if os.path.exists(rf_path):
        with open(rf_path, encoding="utf-8") as f:
            real_fine = json.load(f).get("cells", {})
    fine_agg = {}
    for (lv, hd), v in grid.items():
        for name, lvs in FINE:
            if lv in lvs:
                a = fine_agg.setdefault((hd, name), [0.0] * 4)
                for i in range(4):
                    a[i] += v[i]
    if real_fine:
        print("\n## 细带对照表（同 real_legs_cells.json 的带：1-3 / 4-5 / 6-8 / 9-10）")
        print(f"{'人头':>4} {'Lv带':>6} | {'手数/局':>8} {'成功手':>7} {'home':>7} {'festa':>7} "
              f"{'合计':>8} | 真机 home/festa/合计 (n)")
        print("-" * 100)
        for hd in (0, 1, 2):
            for b, _ in FINE:
                a = fine_agg.get((hd, b))
                if not a or a[1] <= 0:
                    continue
                home, festa = a[2] / a[1], a[3] / a[1]
                ref = real_fine.get(f"{b}|头{hd}")
                refs = (f"{ref['home']:.1f}/{ref['festa']:.1f}/{ref['realized']:.1f} (n={ref['n']})"
                        if ref else "—")
                print(f"{hd:>4} {b:>6} | {a[0]/n:>8.2f} {a[1]:>7.0f} {home:>7.1f} {festa:>7.1f} "
                      f"{home+festa:>8.1f} | {refs}")

    print("\n## 判据 A：festa 腿的 Lv 倍率（同人头，Lv带 2-5 → 9-10）；真机 = 5.8×")
    for hd in (0, 1, 2, 3):
        lo, hi = agg.get((hd, "2-5")), agg.get((hd, "9-10"))
        if not lo or not hi or lo[1] <= 0 or hi[1] <= 0:
            continue
        rl, rh = lo[3] / lo[1], hi[3] / hi[1]
        hl, hh = lo[2] / lo[1], hi[2] / hi[1]
        mid = agg.get((hd, "6-8"))
        mids = f"，6-8 带 {mid[3]/mid[1]:.1f}" if mid and mid[1] else ""
        print(f"  人头{hd}: festa {rl:.1f} →{mids}→ {rh:.1f} = **{rh/max(rl,1e-9):.2f}×**"
              f"   | home {hl:.1f} → {hh:.1f} = {(hh/max(hl,1e-9)):.2f}×（真机 home 1.4×）")

    print("\n## 判据 B：home 腿 / festa 腿的水位比值（引擎 / 真机，同人头同 Lv带）")
    for (hd, b), (rn, rh, rf) in sorted(REAL.items()):
        a = agg.get((hd, b))
        if not a or a[1] <= 0:
            print(f"  人头{hd} Lv{b}: 引擎无该格样本（真机 n={rn}）")
            continue
        home, festa = a[2] / a[1], a[3] / a[1]
        print(f"  人头{hd} Lv{b}: home {home:.1f}/{rh} = {home/rh:.2f}×  ; "
              f"festa {festa:.1f}/{rf} = {festa/rf:.2f}×")

    print("\n## 闭合自检")
    tot_all = sum(v[0] for v in grid.values())
    tot_ok = sum(v[1] for v in grid.values())
    tot_pt = sum(v[2] + v[3] for v in grid.values())
    per_run = tot_pt / n
    ratio = per_run / KEIKO_PT_PER_RUN
    print(f"  稽古手数：全手 {tot_all/n:.2f}/局、成功手 {tot_ok/n:.2f}/局")
    print(f"  Σ格子 (手×(home+festa)) = {per_run:,.1f} PT/局  vs #80 的 {KEIKO_PT_PER_RUN} "
          f"⇒ {ratio:.4f}（±5% **{'通过' if abs(ratio-1) <= 0.05 else '不通过'}**）")
    ex = sum(v[4] for v in grid.values())
    print(f"  追加の稽古触发 {ex/n:.2f} 手/局 ⇒ 腿外 PT ≈ {ex*EXTRA_KEIKO_PT/n:,.1f}/局"
          f"（不在这两条腿里，是闭合差的一部分）")
    hw = sum(k[1] * v[0] for k, v in grid.items()) / max(tot_all, 1)
    print(f"  人头口径：引擎稽古手人头均值（站位卡数轴）= {hw:.3f} ；"
          f"另一口径（归属该演目的卡组卡数）= {heads_act_mean:.3f}")
    print(f"          真机 n_deck 均值 = {REAL_HEADS_MEAN} ⇒ 比值 {hw/REAL_HEADS_MEAN:.2f}× / "
          f"{heads_act_mean/REAL_HEADS_MEAN:.2f}×")
    hd_dist = {}
    for k, v in grid.items():
        hd_dist[k[1]] = hd_dist.get(k[1], 0.0) + v[0]
    print("  人头分布（全手占比）: " + ", ".join(
        f"{h}:{100*d/tot_all:.1f}%" for h, d in sorted(hd_dist.items())))
    print("  真机人头分布（195 手，`real_legs_cells.json` 的带）: 头0:7.2% 头1:47.2% 头2:42.6% 头3+:3.1%")
    oob_lv = sum(v[0] for k, v in grid.items() if k[0] <= 0 or k[0] > 10)
    oob_hd = sum(v[0] for k, v in grid.items() if k[1] > 4)
    print(f"  越界格手数：Lv≤0/>10 = {oob_lv:,.0f} 手；人头>4 = {oob_hd:,.0f} 手（正常应为 0）")

    # ---- 73% 非人头缺口的分腿归属 ----
    # 逐格残差 = 该格（真机 home+festa − 引擎 home+festa）× **引擎**该格成功手/局。
    # 用引擎手数当权重＝「率项」的定义（#80 的 1,102 就是按引擎手数折的），不是真机手数。
    if real_fine:
        FINE_MAP = {1: "1-3", 2: "1-3", 3: "1-3", 4: "4-5", 5: "4-5",
                    6: "6-8", 7: "6-8", 8: "6-8", 9: "9-10", 10: "9-10"}
        g_home = g_festa = g_tot = 0.0
        unmatched = 0.0
        detail = {}
        for (lv, hd), v in grid.items():
            b = FINE_MAP.get(lv)
            if b is None or v[1] <= 0:
                continue
            ref = real_fine.get(f"{b}|头{hd}")
            if ref is None:
                unmatched += v[1] / n
                continue
            w = v[1] / n  # 引擎该格成功手/局
            eh, ef = v[2] / v[1], v[3] / v[1]
            dh, df = ref["home"] - eh, ref["festa"] - ef
            g_home += dh * w
            g_festa += df * w
            g_tot += (dh + df) * w
            # 同一 Lv带 里 Lv 不同的小格先按手数加权并起来再打印（**不能覆盖**）
            k = (hd, b)
            d = detail.setdefault(k, [0.0, 0.0, 0.0])
            d[0] += w
            d[1] += dh * w
            d[2] += df * w
        print("\n## 73% 非人头缺口的分腿归属（逐格残差 × 引擎手数）")
        print(f"  可比格子合计缺口 = {g_tot:,.1f} PT/局，其中 home 腿 {g_home:,.1f} / festa 腿 {g_festa:,.1f}")
        print(f"  （#80 率项 1,102 PT/局 ⇒ 本表覆盖 {100*g_tot/1102:.0f}%；"
              f"无真机同格的引擎手数 {unmatched:.2f} 手/局 未计入，主要是人头≥3 的格子）")
        print("  逐格明细（手/局 | Δhome 加权 | Δfesta 加权 ⇒ PT/局）：")
        for (hd, b), (w, sh, sf) in sorted(detail.items()):
            print(f"    头{hd} Lv{b:<4}: {w:>5.2f} 手/局  Δhome·手 {sh:>+7.1f}"
                  f"  Δfesta·手 {sf:>+7.1f}  ⇒ 合计 {sh + sf:>+8.1f} PT/局")
    return 0


if __name__ == "__main__":
    sys.exit(main())
