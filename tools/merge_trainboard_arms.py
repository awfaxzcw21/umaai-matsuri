#!/usr/bin/env python3
"""把多进程跑的 `matsuri-dist` 分片合成一份盘面读数（#41 强策档并行用）。

为什么要合：盘面打点在 `train_board` 里只出**均值/占比**，跨进程合不起来。
`arm_strong41.exe` 起额外写一份 `train_board_raw`（= 每层 20 列的**原始计数**），
原始计数可以直接相加，再按 `dist.rs::train_board_mean` 同一个公式出均值 ⇒
合成文件与单进程文件同形，`tools/train_leg_gap_split.py --engine-board` 照吃。

用法：
    python tools/merge_trainboard_arms.py E:/HJM/_keileg/out/s41_k64_s*.json \
        --out E:/HJM/_keileg/out/s41_k64_merged.json

`arm_val41b.exe` 起还会多一份 `train_value_raw`（根估值 vs 人头，30 列），这里一并合并并打
「盘面最满 → 引擎估值最优 → 实际落子」三级阶梯；混了新旧 exe 的分片会直接拒绝。
"""
import argparse
import glob
import json
import sys

COLS = ["boards", "max0", "max1", "max2", "max3", "max4", "max5", "max6", "max7",
        "sum_slot_heads", "chosen_n", "ch0", "ch1", "ch2", "ch3", "ch4", "ch5", "ch6",
        "ch7", "take_max"]
VCOLS = ["roots", "amax0", "amax1", "amax2", "amax3", "amax4", "amax5", "amax6", "amax7",
         "sum_amax_heads", "sum_offer_max_heads", "concord", "discord", "tie",
         "val_h0", "val_h1", "val_h2", "val_h3", "val_h4", "val_h5", "val_h6", "val_h7",
         "slot_n0", "slot_n1", "slot_n2", "slot_n3", "slot_n4", "slot_n5", "slot_n6",
         "slot_n7"]
LAYERS = ["non_camp", "camp"]


def mean_block(raw, n_runs):
    """与 `dist.rs::train_board_mean` 逐式一致（列序见 `COLS`）。"""
    m = max(n_runs, 1)
    out = []
    for layer, r in zip(LAYERS, raw):
        boards = float(r[0])
        chosen = float(r[10])
        out.append({
            "layer": layer,
            "decisions_per_run": boards / m,
            "train_decisions_per_run": chosen / m,
            "max_hist_per_run": [r[1 + h] / m for h in range(8)],
            "chosen_hist_per_run": [r[11 + h] / m for h in range(8)],
            "mean_slot_heads": r[9] / max(boards * 5.0, 1.0),
            "take_max_share": r[19] / max(chosen, 1.0),
            "offer_ge2_share": sum(r[3:9]) / max(boards, 1.0),
            "chosen_ge2_share": sum(r[13:19]) / max(chosen, 1.0),
        })
    return {"columns": COLS, "per_layer": out}


def value_readout(vraw):
    """30 列原始计数（已跨片相加）-> 估值阶梯读数。列序见 `VCOLS`。
    阶梯 = 盘面最满人头 → 引擎估值最优格人头 → 实际落子人头（后者在 `train_board` 里）。"""
    out = []
    for layer, r in zip(LAYERS, vraw):
        roots = max(r[0], 1.0)
        conc, disc, tie = r[11], r[12], r[13]
        out.append({
            "layer": layer,
            "roots": r[0],
            "offer_max_heads": r[10] / roots,
            "value_argmax_heads": r[9] / roots,
            "concordant": conc,
            "discordant": disc,
            "tie": tie,
            "kendall_tau": (conc - disc) / max(conc + disc + tie, 1.0),
            "conc_share": conc / max(conc + disc, 1.0),
            "value_by_head": [
                {"heads": h, "n": r[22 + h], "mean_value": r[14 + h] / r[22 + h]}
                for h in range(8) if r[22 + h] > 0
            ],
        })
    return {"columns": VCOLS, "per_layer": out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parts", nargs="+", help="分片 JSON（含 train_board_raw）")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    paths = []
    for p in a.parts:
        paths.extend(sorted(glob.glob(p)) or [p])
    raw = [[0] * 20, [0] * 20]
    vraw = [[0.0] * 30, [0.0] * 30]
    val_shards = 0
    n_runs = 0
    policies, seeds, gate = set(), [], {k: 0.0 for k in ("five_sum", "stat_score", "pt")}
    diag = [0, 0, 0, 0]
    for p in paths:
        j = json.load(open(p, encoding="utf-8"))
        tb = j.get("train_board_raw")
        if not tb:
            sys.exit("%s 没有 train_board_raw —— 用的是旧臂 exe？（要 arm_strong41.exe 及以后）" % p)
        if tb.get("columns") and tb["columns"] != COLS:
            sys.exit("%s 列序与预期不符：%s" % (p, tb["columns"]))
        if any(len(row) != 20 for row in tb["per_layer"]):
            sys.exit("%s 每层应是 20 列原始计数" % p)
        for acc, row in zip(raw, tb["per_layer"]):
            for i, v in enumerate(row):
                acc[i] += v
        tv = j.get("train_value_raw")
        if tv:
            val_shards += 1
            if tv.get("columns") and tv["columns"] != VCOLS:
                sys.exit("%s 估值列序与预期不符" % p)
            for acc, row in zip(vraw, tv["per_layer"]):
                for i, v in enumerate(row):
                    acc[i] += v
        n_runs += tb["n_runs"]
        policies.add(j.get("policy", "?"))
        seeds.append(j.get("seed_base"))
        g = j.get("gate", {})
        for k in gate:
            gate[k] += g.get(k, {}).get("mean", 0.0) * tb["n_runs"]
        d = j.get("search_diag") or {}
        diag = [diag[i] + d.get(k, 0) for i, k in
                enumerate(("train_steps", "endgame_1ply",
                           "forced_by_festa_deadline", "off_stage"))]
    if len(policies) > 1:
        sys.exit("这些分片不是同一档：%s" % sorted(policies))
    if 0 < val_shards < len(paths):
        sys.exit("%d/%d 片有 train_value_raw：估值打点要整批同 exe，别混旧臂" % (val_shards, len(paths)))

    result = {
        "ok": True,
        "engine": "merge_trainboard_arms",
        "merged_from": [p.replace("\\", "/") for p in paths],
        "policy": policies.pop(),
        "seed_bases": seeds,
        "n": n_runs,
        "search_diag": {
            "train_steps": diag[0],
            "endgame_1ply": diag[1],
            "forced_by_festa_deadline": diag[2],
            "off_stage": diag[3],
        },
        "train_board": mean_block(raw, n_runs),
        "train_board_raw": {"columns": COLS, "n_runs": n_runs, "per_layer": raw},
        # 合成分量只有加权均值（跨分片合不出方差），够用于「强策档是否真的更高」的判读
        "gate_mean": {k: v / max(n_runs, 1) for k, v in gate.items()},
    }
    if val_shards:
        result["train_value_raw"] = {"columns": VCOLS, "n_runs": n_runs, "per_layer": vraw}
        result["train_value"] = value_readout(vraw)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("合并 %d 片 / %d 局 → %s" % (len(paths), n_runs, a.out))
    for e in result["train_board"]["per_layer"]:
        h = e["chosen_hist_per_run"]
        take = sum(i * c for i, c in enumerate(h)) / max(e["train_decisions_per_run"], 1e-9)
        off = sum(i * c for i, c in enumerate(e["max_hist_per_run"])) / max(e["decisions_per_run"], 1e-9)
        print("  %-9s 手/局 %6.2f  take %.3f  offer最满 %.3f  增益 %.3f  取最满 %5.1f%%"
              % (e["layer"], e["train_decisions_per_run"], take, off,
                 take / off if off else 0.0, 100.0 * e["take_max_share"]))
    print("  gate 加权均值 %s" % {k: round(v, 2) for k, v in result["gate_mean"].items()})
    if val_shards:
        print("\n估值阶梯（同一批根决策：盘面最满 → 引擎估值最优 → 实际落子）")
        for e, b in zip(result["train_value"]["per_layer"], result["train_board"]["per_layer"]):
            hh = b["chosen_hist_per_run"]
            take = sum(i * c for i, c in enumerate(hh)) / max(b["train_decisions_per_run"], 1e-9)
            print("  %-9s 根 %5.0f/局  盘面最满 %.3f 头 → 估值最优 %.3f 头 → 实际落子 %.3f 头"
                  % (e["layer"], e["roots"] / max(n_runs, 1), e["offer_max_heads"],
                     e["value_argmax_heads"], take))
            print("            人头 vs 估值 一致 %d / 不一致 %d（一致率 %.1f%%，τ=%.3f）"
                  % (e["concordant"], e["discordant"], 100.0 * e["conc_share"], e["kendall_tau"]))
            print("            每档人头的估值均值 " + "  ".join(
                "h%d:%.0f(n=%d)" % (v["heads"], v["mean_value"], v["n"])
                for v in e["value_by_head"] if v["n"] >= 20))


if __name__ == "__main__":
    main()
