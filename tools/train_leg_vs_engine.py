"""#40 的对账表：真机板上报文「训练腿预告」 vs 引擎 `training_leg` 同位置分解。

两边按 (训练Lv, 卡组人头) 分桶逐格对照，**条件化后策略选择与 Lv 分布都不进比较**
（引擎只走它选的训练位、真机每块板六个位全在报文里，直接比总量会被选择偏置污染）。

    python tools/train_leg_vs_engine.py            # 默认读 out/train_leg_preview_20261103.csv
    python tools/train_leg_vs_engine.py --engine E:/HJM/_keileg/out_head4000/trainleg_result.json

真机侧：`out/train_leg_preview_20261103.csv`（`tools/train_leg_preview.py` 生成，只取 `kind=plain`
且 `chosen=1` 之外的全量——板上每个位都是合法预告；分桶键 = `level` × `heads_deck`）。
引擎侧：测量壳 `_keileg` 的 `train_legs.plain`（同一条 `training_leg` 公式在结算处的分解）。
"""

import argparse
import collections
import csv
import json
import statistics

AXES = ("speed", "stamina", "power", "guts", "wiz")


def real_cells(path, kind="plain"):
    out = collections.defaultdict(lambda: {"pt": [], "five": [], "vit": [], "n": 0})
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        if r["kind"] != kind:
            continue
        lv, hd = int(r["level"]), int(r["heads_deck"])
        pt = int(r["pt"])
        five = sum(int(r[a] or 0) for a in AXES)
        c = out[(lv, hd)]
        c["pt"].append(pt)
        c["five"].append(five)
        c["vit"].append(int(r["vital"] or 0))
        c["n"] += 1
    return out


def engine_cells(path, slot="plain"):
    j = json.load(open(path, encoding="utf-8"))["train_legs"]
    out = {}
    for r in j["per_cell"]:
        if r["slot"] != slot:
            continue
        out[(r["lv"], r["heads"])] = r
    return out, j["hands_per_run"]


def show(real, eng, label):
    print("\n== %s：真机 vs 引擎，按 (训练Lv x 卡组人头) 逐格 ==" % label)
    print("%-8s %5s %6s %6s %7s %7s %7s %7s %7s %7s" % (
        "格", "真n", "真Pt", "引Pt", "Pt比", "真Σ五维", "引Σ五维", "五维比", "真Pt/五", "引Pt/五"))
    tot = {"rpt": 0.0, "ept": 0.0, "rf": 0.0, "ef": 0.0, "n": 0, "wn": 0}
    for k in sorted(set(real) & set(eng)):
        r, e = real[k], eng[k]
        if r["n"] < 5:
            continue
        rpt, rf = statistics.median(r["pt"]), statistics.median(r["five"])
        ratio_pt = e["pt"] / rpt if rpt else float("nan")
        ratio_f = e["five"] / rf if rf else float("nan")
        print("%-8s %5d %6.1f %6.1f %7.2f %7.1f %7.1f %7.2f %7.3f %7.3f" % (
            "L%d×%d" % k, r["n"], rpt, e["pt"], ratio_pt, rf, e["five"], ratio_f,
            rpt / rf if rf else float("nan"), e["pt_over_five"]))
        # 加权合计：以真机该格样本数为权重
        tot["rpt"] += rpt * r["n"]; tot["ept"] += e["pt"] * r["n"]
        tot["rf"] += rf * r["n"]; tot["ef"] += e["five"] * r["n"]
        tot["n"] += r["n"]; tot["wn"] += 1
    if tot["n"]:
        print("-" * 78)
        print("按真机样本数加权（%d 格 / %d 条）：真Pt %.1f vs 引 %.1f ⇒ 引擎/真机 = %.3f" % (
            tot["wn"], tot["n"], tot["rpt"] / tot["n"], tot["ept"] / tot["n"], tot["ept"] / tot["rpt"]))
        print("同权重五维：真 %.1f vs 引 %.1f ⇒ %.3f｜Pt/五维比值 真 %.3f 引 %.3f" % (
            tot["rf"] / tot["n"], tot["ef"] / tot["n"], tot["ef"] / tot["rf"],
            tot["rpt"] / tot["rf"], tot["ept"] / tot["ef"]))


def slopes(real, eng, label):
    """人头斜率：0 人头那一列当锚，看每多一个卡组人头加多少。"""
    print("\n== %s：人头斜率（同 Lv，相对 0 人头）==" % label)
    for lv in sorted({k[0] for k in real}):
        def curve(src, key):
            pts = []
            for (l, h), v in src.items():
                if l != lv:
                    continue
                val = key(v)
                if val is not None:
                    pts.append((h, val))
            return sorted(p for p in pts if p[1] and p[1] == p[1])
        rp = [(h, statistics.median(v["pt"])) for (l, h), v in real.items() if l == lv and v["n"] >= 5]
        ep = [(h, v["pt"]) for (l, h), v in eng.items() if l == lv]
        rf = [(h, statistics.median(v["five"])) for (l, h), v in real.items() if l == lv and v["n"] >= 5]
        ef = [(h, v["five"]) for (l, h), v in eng.items() if l == lv]
        if len(rp) < 2 or len(ep) < 2:
            continue
        print("  Lv%d 真Pt %s → 斜率 %.2f/头｜引擎 %s → 斜率 %.2f/头" % (
            lv, [(h, p) for h, p in rp], slope(rp), [(h, round(p, 1)) for h, p in ep], slope(ep)))
        if len(rf) >= 2 and len(ef) >= 2:
            print("       真五维斜率 %.2f/头｜引擎五维斜率 %.2f/头" % (slope(rf), slope(ef)))


def slope(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def rekey_heads(cells):
    """把 (lv, heads) 桶并成 heads 桶（只保留可比维度）。"""
    out = collections.defaultdict(lambda: {"pt": [], "five": [], "vit": [], "n": 0})
    for (_lv, hd), v in cells.items():
        c = out[(1, hd)]
        c["pt"] += v["pt"]
        c["five"] += v["five"]
        c["vit"] += v["vit"]
        c["n"] += v["n"]
    return out


def rekey_heads_eng(eng):
    """引擎侧同样并到 heads，用手数当权重（保持每手均值口径）。"""
    acc = collections.defaultdict(lambda: {"pt": 0.0, "five": 0.0, "n": 0.0})
    for (_lv, hd), r in eng.items():
        a = acc[(1, hd)]
        a["pt"] += r["pt"] * r["hands"]
        a["five"] += r["five"] * r["hands"]
        a["n"] += r["hands"]
    return {
        k: {
            "pt": v["pt"] / v["n"],
            "five": v["five"] / v["n"],
            "hands": v["n"],
            "pt_over_five": v["pt"] / max(v["five"], 1e-9),
        }
        for k, v in acc.items()
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", default="out/train_leg_preview_20261103.csv")
    ap.add_argument("--engine", default="E:/HJM/_keileg/out_head4000/trainleg_result.json")
    a = ap.parse_args()
    real = real_cells(a.real, "plain")
    eng, hpr = engine_cells(a.engine, "plain")
    print("引擎普通训练手数/局 = %.2f｜真机板上普通训练预告 %d 条" % (
        hpr["plain"], sum(v["n"] for v in real.values())))
    show(real, eng, "普通训练（全板每位）")
    slopes(real, eng, "普通训练")

    # 稽古 home 腿：板上报文的 `level` 对 5101-5105 是**演目 Lv**，引擎记的是**训练 Lv**，
    # 两边不同量纲 ⇒ 只按人头分桶（卡组人头两边语义一致），Lv 侧不对账。
    engk, hprk = engine_cells(a.engine, "keiko_home")
    realk = rekey_heads(real_cells(a.real, "keiko"))
    engkh = rekey_heads_eng(engk)
    print("\n引擎稽古 home 腿手数/局 = %.2f｜真机稽古 home 腿预告 %d 条" % (
        hprk["keiko_home"], sum(v["n"] for v in realk.values())))
    show(realk, engkh, "稽古 home 腿（只按人头分桶，Lv 不同量纲）")


if __name__ == "__main__":
    main()
