"""#56 分身定价：三档剂量的逐格残差双差分（DiD）+ 排一(placebo)。

面板自报的 `stat_gains` 是结算前预告，引擎 `train_preview` 是同一条腿的预告，
两者之差＝该格的引擎偏差。分身格（treatment）与非分身格（control）在**同一块板**里，
三档剂量只动分身格（非分身格逐字节相同，脚本末尾自检），所以

    DiD(axis) = mean(pkt−eng | 分身格) − mean(pkt−eng | 非分身格)

把「引擎对这块板的整体训练偏差」这个共因减掉，剩下的就是**分身那一格值多少**。
哪一档把 DiD 压到 0 附近，哪一档对真机；不引入任何新常数。

**识别假设**（结论的范围由它决定）：同一块板内，引擎的偏差在分身格与非分身格相同。
这条由 placebo 检验：把非分身格随机分成同样大小的两组，DiD 应当 ≈0——
它就是本方法能分辨的最小剂量，DiD 小于它的一律读成「分辨不了」。

    python tools/clone_dose_did.py
    python tools/clone_dose_did.py --runs 168,170    # 只看某几局
"""

import argparse
import csv
import json
import random
import statistics
import sys
from collections import Counter, defaultdict

DOSSES = ["heads_only", "card_effects", "card_effects_friendship"]
AXES = ["速度", "耐力", "力量", "根性", "智力"]
CARD_TYPE = {}


def read(dose):
    """→ {(board, cell): row}，只留普通训练行 ∧ 面板确实报了五轴收益的格。"""
    out = {}
    with open(f"out/board_{dose}.tsv", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r["kind"] != "plain":
                continue
            if any(int(r[f"pkt{i}"]) < 0 for i in range(5)):
                continue
            clones = []
            if r["clone_cards"] != "-":
                clones = [int(x.split(":")[1]) for x in r["clone_cards"].split("|")]
            out[(r["board"], int(r["cell"]))] = {
                "heads": int(r["heads"]),
                "train": r["train"],
                "clones": clones,
                "eng": [int(r[f"eng{i}"]) for i in range(5)],
                "pkt": [int(r[f"pkt{i}"]) for i in range(5)],
            }
    return out


def residuals(row):
    return [row["pkt"][i] - row["eng"][i] for i in range(5)]


def group_means(rows):
    treat = defaultdict(list)
    ctrl = defaultdict(list)
    for row in rows.values():
        res = residuals(row)
        bucket = treat if row["clones"] else ctrl
        for i in range(5):
            bucket[i].append(res[i])
    return treat, ctrl


def did(treat, ctrl):
    out = []
    for i in range(5):
        if not treat[i] or not ctrl[i]:
            out.append(float("nan"))
        else:
            out.append(statistics.fmean(treat[i]) - statistics.fmean(ctrl[i]))
    return out


def placebo(rows, n=400, seed=7):
    """非分身格随机两组之差 ⇒ 本方法的噪声地板（同分布下真值应为 0）。"""
    rng = random.Random(seed)
    ctrl = [residuals(v) for v in rows.values() if not v["clones"]]
    n_t = sum(1 for v in rows.values() if v["clones"])
    if n_t == 0 or len(ctrl) < 2 * n_t:
        return [float("nan")] * 5
    out = []
    for i in range(5):
        vals = [r[i] for r in ctrl]
        diffs = []
        for _ in range(n):
            rng.shuffle(vals)
            diffs.append(statistics.fmean(vals[:n_t]) - statistics.fmean(vals[-n_t:]))
        out.append(statistics.fmean([abs(d) for d in diffs]))
    return out


def bootstrap(tables, n=2000, seed=11):
    """**按板**整块重抽（cluster bootstrap），给每轴 DiD 一个 95% 区间。小样本必须报区间不报点。

    27 块板出 35 个分身格，同板内的格共享这块板的引擎偏差 ⇒ 按格独立重抽会把区间算窄。
    逐档各自取表：三档只有分身格不同，区间也必须有别，逐位相同即说明抽错了表。
    """
    rng = random.Random(seed)
    out = {}
    for dose in DOSSES:
        boards = defaultdict(list)
        for key, row in tables[dose].items():
            boards[key[0]].append(row)
        names = list(boards)
        draws = defaultdict(list)
        for _ in range(n):
            pick = [row for b in rng.choices(names, k=len(names)) for row in boards[b]]
            treat = [row for row in pick if row["clones"]]
            ctrl = [row for row in pick if not row["clones"]]
            if not treat or not ctrl:
                continue
            for i in range(5):
                tm = statistics.fmean(residuals(row)[i] for row in treat)
                cm = statistics.fmean(residuals(row)[i] for row in ctrl)
                draws[i].append(tm - cm)
        out[dose] = [
            (sorted(v)[int(0.025 * len(v))], sorted(v)[int(0.975 * len(v)) - 1])
            if (v := draws[i]) else (float("nan"), float("nan"))
            for i in range(5)
        ]
    return out


def stratified_did(rows, key="train"):
    """按训练位分层后再加权平均——去掉「分身格与非分身格构成的训练位不同」这个混淆。

    每条腿的量级本身随训练位变（速度腿大、智力腿小），所以**绝对残差的共因也随训练位变**；
    不分层的 DiD 会把「引擎在某个训练位上的整体偏差」错记成分身定价。
    """
    strata = defaultdict(lambda: (defaultdict(list), defaultdict(list)))
    for v in rows.values():
        res = residuals(v)
        treat, ctrl = strata[v[key]]
        for i in range(5):
            (treat if v["clones"] else ctrl)[i].append(res[i])
    out = []
    for i in range(5):
        acc = weight = 0.0
        for treat, ctrl in strata.values():
            if not treat[i] or not ctrl[i]:
                continue
            acc += len(treat[i]) * (statistics.fmean(treat[i]) - statistics.fmean(ctrl[i]))
            weight += len(treat[i])
        out.append(acc / weight if weight else float("nan"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=None, help="只看这些 run 的板，逗号分隔")
    args = ap.parse_args()
    CARD_TYPE.update({int(k): int(v["cardType"])
                      for k, v in json.load(open("data/cardDB.json", encoding="utf-8")).items()})
    want = set(args.runs.split(",")) if args.runs else None

    tables = {d: read(d) for d in DOSSES}
    rows0 = tables[DOSSES[0]]
    if want:
        keep = {k for k in rows0 if k[0].split("_")[0] in want}
        tables = {d: {k: v for k, v in t.items() if k in keep} for d, t in tables.items()}
        rows0 = tables[DOSSES[0]]

    n_t = sum(1 for v in rows0.values() if v["clones"])
    n_c = len(rows0) - n_t
    print(f"板 {len({k[0] for k in rows0})} 块｜普通训练行分身格 {n_t}｜非分身格 {n_c}\n")

    print(f"{'dose':<26}" + "".join(f"{a:>9}" for a in AXES) + f"{'Σ DiD':>10}")
    for d in DOSSES:
        treat, ctrl = group_means(tables[d])
        dd = did(treat, ctrl)
        print(f"{d:<26}" + "".join(f"{v:>9.2f}" for v in dd) + f"{sum(dd):>10.2f}")
    pf = placebo(rows0)
    print(f"{'placebo 噪声地板(|DiD|)':<26}" + "".join(f"{v:>9.2f}" for v in pf))

    from collections import Counter
    dist = Counter(v["train"] for v in rows0.values() if v["clones"])
    print("\n按训练位分层的 DiD（去掉「分身格集中在某些训练位」这个构成混淆）")
    print("　分身格的训练位分布：" + "｜".join(f"{t} {n}" for t, n in sorted(dist.items()))
          + f"　（非分身格：" + "｜".join(f"{t} {n}" for t, n in sorted(
              Counter(v["train"] for v in rows0.values() if not v["clones"]).items())) + "）")
    print(f"{'dose':<26}" + "".join(f"{a:>9}" for a in AXES) + f"{'Σ DiD':>10}")
    for d in DOSSES:
        dd = stratified_did(tables[d])
        print(f"{d:<26}" + "".join(f"{v:>9.2f}" for v in dd) + f"{sum(dd):>10.2f}")

    print("\n95% bootstrap 区间（按板整块重抽 2000 次；点估计同上）：")
    for dose, ci in bootstrap(tables).items():
        print(f"{dose:<26}" + "".join(f"{lo:>6.1f},{hi:<6.1f}" for lo, hi in ci))

    print("\n每撤/加一条腿给分身格的**引擎增量**（Δeng 相对 card_effects 档的均值；这就是定价本身）：")
    base_rows = tables["card_effects"]
    tk = [k for k in base_rows if base_rows[k]["clones"]]
    for label, other in (("heads_only − card_effects（撤掉词条腿）", "heads_only"),
                         ("friendship − card_effects（再加友情腿）", "card_effects_friendship")):
        deltas = [[tables[other][k]["eng"][i] - base_rows[k]["eng"][i] for i in range(5)]
                  for k in tk]
        m = [statistics.fmean(x[i] for x in deltas) for i in range(5)]
        print(f"{label:<40}" + "".join(f"{v:>9.2f}" for v in m) + f"{sum(m):>10.2f}")

    print("\n目标残差：只看分身卡**自己的得意轴**那一格那一轴（pkt−eng，扣共因前）")
    print(f"{'dose':<26}" + "".join(f"{a:>13}" for a in AXES))
    for d in DOSSES:
        cells = []
        for i in range(5):
            vals = []
            for v in tables[d].values():
                if i not in [CARD_TYPE.get(c, -1) for c in v["clones"]]:
                    continue
                vals.append(residuals(v)[i])
            cells.append(f"{statistics.fmean(vals):>6.2f}/{len(vals):<3}" if vals else f"{'-':>10}")
        print(f"{d:<26}" + "".join(f"{c:>13}" for c in cells))

    # 自检：剂量只该动分身格
    a, b, c = (tables[d] for d in DOSSES)
    ctrl_keys = [k for k in a if not a[k]["clones"]]
    bad = [k for k in ctrl_keys if not (a[k]["eng"] == b[k]["eng"] == c[k]["eng"])]
    treated = [k for k in a if a[k]["clones"]]
    moved = [k for k in treated if a[k]["eng"] != c[k]["eng"]]
    print(f"\n自检：非分身格 {len(ctrl_keys)} 个里三档 eng 不同的 **{len(bad)}** 个（应为 0）"
          f"｜分身格 {len(treated)} 个里 heads_only↔friendship 有差的 {len(moved)} 个（应≈全量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
